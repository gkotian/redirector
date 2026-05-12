import base64
import json
import os
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import SplitResult
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

CONFIG_PATH = Path(__file__).parent / "config.json"
CLUSTER_DOMAIN = "example.com"
AZURE_DEVOPS_PAT_ENV = "AZURE_DEVOPS_PAT"
AZURE_DEVOPS_PAT_FILE_ENV = "AZURE_DEVOPS_PAT_FILE"
AZURE_DEVOPS_API_VERSION = "7.1"
AZURE_DEVOPS_TIMEOUT_SECONDS = 5
azure_build_definition_cache = {}
azure_build_resolution_errors = {}


def entry_kubernetes_workload_name(entry):
    return entry.get("kubernetes_workload_name") or entry.get("datadog_service_name")


def parse_rancher_resource(parsed):
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]

    if "explorer" in parts:
        explorer_index = parts.index("explorer")
        if len(parts) > explorer_index + 3:
            resource_type = parts[explorer_index + 1]
            namespace = parts[explorer_index + 2]
            workload_name = parts[explorer_index + 3]
            return resource_type, namespace, workload_name

    if len(parts) >= 2:
        return "", parts[-2], parts[-1]

    return "", "", ""


def parse_rancher_workload(parsed):
    _, namespace, workload_name = parse_rancher_resource(parsed)
    return namespace, workload_name


def cronjob_workload_name_from_job_name(workload_name):
    cronjob_name, _, run_suffix = workload_name.rpartition("-")
    if cronjob_name and run_suffix.isdigit():
        return cronjob_name
    return ""


def identify_rancher_app(parsed):
    resource_type, namespace, workload_name = parse_rancher_resource(parsed)
    entry = by_rancher.get((namespace, workload_name))
    if entry:
        return entry

    if resource_type == "batch.job":
        cronjob_name = cronjob_workload_name_from_job_name(workload_name)
        if cronjob_name:
            entry = by_rancher.get((namespace, cronjob_name))
            if entry and entry.get("kubernetes_workload_type") == "cronjob":
                return entry

    return None


def parse_datadog_service(parsed):
    query = parse_qs(parsed.query)
    q = query.get("query", [""])[0]
    for part in q.split(" "):
        if part.startswith("service:"):
            return part.split(":", 1)[1]
    return ""


def azure_build_cache_key(org, project, build_id):
    return org, project, build_id


def remember_azure_build_resolution_error(org, project, build_id, message):
    key = azure_build_cache_key(org, project, build_id)
    azure_build_resolution_errors[key] = message


def azure_build_resolution_error(org, project, build_id):
    key = azure_build_cache_key(org, project, build_id)
    return azure_build_resolution_errors.get(key)


def read_azure_devops_pat():
    env_pat = os.environ.get(AZURE_DEVOPS_PAT_ENV, "").strip()
    if env_pat:
        return env_pat, ""

    pat_file = os.environ.get(AZURE_DEVOPS_PAT_FILE_ENV, "").strip()
    if not pat_file:
        return (
            "",
            f"set {AZURE_DEVOPS_PAT_ENV} or {AZURE_DEVOPS_PAT_FILE_ENV}",
        )

    pat_path = Path(pat_file)
    try:
        file_pat = pat_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "", f"Azure DevOps PAT file {pat_path} does not exist"
    except OSError as exc:
        return "", f"could not read Azure DevOps PAT file {pat_path}: {exc}"

    if not file_pat:
        return "", f"Azure DevOps PAT file {pat_path} is empty"

    return file_pat, ""


def describe_azure_devops_pat_source():
    if os.environ.get(AZURE_DEVOPS_PAT_ENV, "").strip():
        return f"{AZURE_DEVOPS_PAT_ENV} is set"

    pat_file = os.environ.get(AZURE_DEVOPS_PAT_FILE_ENV, "").strip()
    if not pat_file:
        return (
            "Azure DevOps buildId resolution disabled; "
            f"set {AZURE_DEVOPS_PAT_ENV} or {AZURE_DEVOPS_PAT_FILE_ENV}"
        )

    pat_path = Path(pat_file)
    if pat_path.is_file():
        return f"Azure DevOps PAT file configured at {pat_path}"

    return f"Azure DevOps PAT file configured but missing: {pat_path}"


def build_azure_devops_request(org, project, build_id, pat):
    auth_value = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
    url = (
        "https://dev.azure.com/"
        + quote(org, safe="")
        + "/"
        + quote(project, safe="")
        + "/_apis/build/builds/"
        + quote(build_id, safe="")
        + "?api-version="
        + AZURE_DEVOPS_API_VERSION
    )
    return Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": "Basic " + auth_value,
        },
    )


def resolve_definition_id_from_build_id(org, project, build_id):
    key = azure_build_cache_key(org, project, build_id)
    if key in azure_build_definition_cache:
        return azure_build_definition_cache[key]

    azure_build_resolution_errors.pop(key, None)
    pat, pat_error = read_azure_devops_pat()
    if not pat:
        remember_azure_build_resolution_error(
            org,
            project,
            build_id,
            pat_error,
        )
        return ""

    request = build_azure_devops_request(org, project, build_id, pat)
    try:
        with urlopen(request, timeout=AZURE_DEVOPS_TIMEOUT_SECONDS) as response:
            build = json.load(response)
    except HTTPError as exc:
        remember_azure_build_resolution_error(
            org,
            project,
            build_id,
            f"Azure DevOps API returned HTTP {exc.code}",
        )
        return ""
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        remember_azure_build_resolution_error(
            org,
            project,
            build_id,
            "Azure DevOps API request failed: " + str(exc),
        )
        return ""

    definition_id = str(build.get("definition", {}).get("id") or "")
    if not definition_id:
        remember_azure_build_resolution_error(
            org,
            project,
            build_id,
            "Azure DevOps build response did not include a definition id",
        )
        return ""

    azure_build_definition_cache[key] = definition_id
    return definition_id


def resolve_pipeline_id(org, project, query):
    pipeline_id = query.get("definitionId", [""])[0]
    if pipeline_id:
        return pipeline_id

    build_id = query.get("buildId", [""])[0]
    if build_id:
        return resolve_definition_id_from_build_id(org, project, build_id)

    return ""


def load_config():
    with open(CONFIG_PATH) as f:
        orgs = json.load(f)

    by_datadog_service = {}
    by_rancher = {}
    by_repo = {}
    by_pipeline = {}
    by_api_hostname = {}

    for org_entry in orgs:
        org = org_entry["azure_devops_org"]
        for project in org_entry["azure_devops_projects"]:
            project_name = project["project_name"]
            namespace = project["rancher_namespace"]
            for app in project["apps"]:
                entry = {
                    "azure_devops_org": org,
                    "project_name": project_name,
                    "rancher_namespace": namespace,
                    "repo_name": app["repo_name"],
                    "pipeline_id": app.get("pipeline_id"),
                    "datadog_service_name": app.get("datadog_service_name"),
                    "kubernetes_workload_name": app.get("kubernetes_workload_name"),
                    "api_hostname": app.get("api_hostname"),
                    "is_deployed": app.get(
                        "is_deployed",
                        bool(
                            app.get("datadog_service_name")
                            or app.get("kubernetes_workload_name")
                        ),
                    ),
                    "kubernetes_workload_type": app.get("kubernetes_workload_type"),
                }
                if entry["is_deployed"] and entry["datadog_service_name"]:
                    by_datadog_service[entry["datadog_service_name"]] = entry
                workload_name = entry_kubernetes_workload_name(entry)
                if entry["is_deployed"] and namespace and workload_name:
                    by_rancher[(namespace, workload_name)] = entry
                by_repo[
                    (org, project_name, app["repo_name"])
                ] = entry
                if entry["pipeline_id"]:
                    by_pipeline[(org, project_name, entry["pipeline_id"])] = entry
                if entry["api_hostname"]:
                    by_api_hostname[entry["api_hostname"]] = entry

    return by_datadog_service, by_rancher, by_repo, by_pipeline, by_api_hostname


def normalize_azure_devops_url(url):
    parsed = urlparse(url)
    host = parsed.hostname or ""

    if not host.endswith(".visualstudio.com"):
        return parsed

    org = host.removesuffix(".visualstudio.com")
    path = parsed.path
    if not path.startswith("/"):
        path = "/" + path

    normalized = SplitResult(
        scheme=parsed.scheme or "https",
        netloc="dev.azure.com",
        path=f"/{org}{path}",
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return urlparse(normalized.geturl())


def identify_app(url):
    parsed = normalize_azure_devops_url(url)
    host = parsed.hostname or ""

    if "rancher" in host:
        return identify_rancher_app(parsed)

    if "datadoghq" in host:
        service = parse_datadog_service(parsed)
        if service:
            return by_datadog_service.get(service)

    if CLUSTER_DOMAIN in host and "rancher" not in host:
        # API endpoint URL, e.g. mongodbgateway.example.com
        # or mongodbgateway.dev.example.com
        hostname = host.replace(".dev." + CLUSTER_DOMAIN, "")
        hostname = hostname.replace("." + CLUSTER_DOMAIN, "")
        return by_api_hostname.get(hostname)

    if "dev.azure.com" in host:
        path = parsed.path.strip("/").split("/")
        # URL pattern: /{org}/{project}/_git/{repo}
        #          or: /{org}/{project}/_build?definitionId=...
        if len(path) >= 3:
            org = path[0]
            project = path[1]
            if path[2] == "_build":
                query = parse_qs(parsed.query)
                pipeline_id = resolve_pipeline_id(org, project, query)
                if pipeline_id:
                    return by_pipeline.get((org, project, pipeline_id))
            repo = path[3] if len(path) > 3 else None
            if repo:
                return by_repo.get((org, project, repo))

    return None


def describe_source(url):
    parsed = normalize_azure_devops_url(url)
    host = parsed.hostname or ""

    if "rancher.dev" in host:
        return "Rancher (dev)"

    if "rancher" in host:
        return "Rancher (prod)"

    if "datadoghq" in host:
        return "Datadog"

    if CLUSTER_DOMAIN in host and "rancher" not in host:
        if ".dev." + CLUSTER_DOMAIN in host:
            return "API (dev)"
        return "API (prod)"

    if "dev.azure.com" in host:
        path = parsed.path.strip("/").split("/")
        if len(path) >= 3 and path[2] == "_build":
            return "Pipeline"
        if len(path) >= 3 and path[2] == "_git":
            return "Repo"

    return host or "Unknown"


def describe_destination(destination):
    labels = {
        "datadog": "Datadog",
        "rancher": "Rancher (prod)",
        "rancher-dev": "Rancher (dev)",
        "repo": "Repo",
        "pipeline": "Pipeline",
        "api": "API (prod)",
        "api-dev": "API (dev)",
    }
    return labels.get(destination, destination)


def describe_mapping_failure(url):
    parsed = normalize_azure_devops_url(url)
    host = parsed.hostname or ""

    if "rancher" in host:
        namespace, workload_name = parse_rancher_workload(parsed)
        if namespace and workload_name:
            return (
                "No mapping found for Rancher workload "
                f"{namespace}/{workload_name}."
            )

    if "datadoghq" in host:
        service = parse_datadog_service(parsed)
        if service:
            return f"No mapping found for Datadog service {service}."

    if CLUSTER_DOMAIN in host and "rancher" not in host:
        hostname = host.replace(".dev." + CLUSTER_DOMAIN, "")
        hostname = hostname.replace("." + CLUSTER_DOMAIN, "")
        if hostname:
            return f"No mapping found for API hostname {hostname}."

    if "dev.azure.com" in host:
        path = parsed.path.strip("/").split("/")
        if len(path) >= 3 and path[2] == "_build":
            query = parse_qs(parsed.query)
            definition_id = query.get("definitionId", [""])[0]
            if definition_id:
                return f"No mapping found for pipeline definitionId {definition_id}."
            build_id = query.get("buildId", [""])[0]
            if build_id and len(path) >= 2:
                org = path[0]
                project = path[1]
                resolved_definition_id = azure_build_definition_cache.get(
                    azure_build_cache_key(org, project, build_id)
                )
                if resolved_definition_id:
                    return (
                        f"No mapping found for pipeline buildId {build_id} "
                        f"(definitionId {resolved_definition_id})."
                    )
                error = azure_build_resolution_error(org, project, build_id)
                if error:
                    return (
                        f"Could not resolve Azure DevOps buildId {build_id}: "
                        f"{error}."
                    )
                return f"No mapping found for pipeline buildId {build_id}."
        if len(path) > 3:
            return f"No mapping found for repo {path[0]}/{path[1]}/{path[3]}."

    return "No mapping found for this URL"


def source_debug_fields(url):
    if not url:
        return {}

    parsed = normalize_azure_devops_url(url)
    host = parsed.hostname or ""
    fields = {
        "source_host": host,
        "source_path": parsed.path,
    }

    if "rancher" in host:
        resource_type, namespace, workload_name = parse_rancher_resource(parsed)
        fields["rancher_resource_type"] = resource_type
        fields["rancher_namespace"] = namespace
        fields["kubernetes_workload_name"] = workload_name
    elif "datadoghq" in host:
        fields["datadog_service_name"] = parse_datadog_service(parsed)
    elif CLUSTER_DOMAIN in host:
        hostname = host.replace(".dev." + CLUSTER_DOMAIN, "")
        hostname = hostname.replace("." + CLUSTER_DOMAIN, "")
        fields["api_hostname"] = hostname
    elif "dev.azure.com" in host:
        path = parsed.path.strip("/").split("/")
        if len(path) >= 2:
            fields["azure_devops_org"] = path[0]
            fields["project_name"] = path[1]
        if len(path) >= 3 and path[2] == "_build":
            query = parse_qs(parsed.query)
            definition_id = query.get("definitionId", [""])[0]
            build_id = query.get("buildId", [""])[0]
            if definition_id:
                fields["pipeline_id"] = definition_id
            if build_id:
                fields["build_id"] = build_id
                resolved_definition_id = azure_build_definition_cache.get(
                    azure_build_cache_key(path[0], path[1], build_id)
                )
                if resolved_definition_id:
                    fields["pipeline_id"] = resolved_definition_id
                error = azure_build_resolution_error(path[0], path[1], build_id)
                if error:
                    fields["pipeline_resolution_error"] = error
        elif len(path) > 3:
            fields["repo_name"] = path[3]

    return fields


def destination_is_supported(entry, destination):
    if destination == "datadog":
        return bool(entry.get("is_deployed") and entry.get("datadog_service_name"))

    if destination in ("rancher", "rancher-dev"):
        return bool(
            entry.get("is_deployed")
            and entry.get("rancher_namespace")
            and entry_kubernetes_workload_name(entry)
            and entry.get("kubernetes_workload_type") in {"deployment", "cronjob"}
        )

    if destination == "repo":
        return True

    if destination == "pipeline":
        return bool(entry.get("pipeline_id"))

    if destination in ("api", "api-dev"):
        return bool(entry.get("api_hostname"))

    return False


def describe_unsupported_destination(entry, destination):
    repo_name = entry["repo_name"]

    if destination in ("rancher", "rancher-dev"):
        if entry.get("is_deployed") and entry.get("kubernetes_workload_type") not in {
            "deployment",
            "cronjob",
        }:
            return (
                f"No {destination} redirect is configured for {repo_name}. "
                "This deployed app is missing a valid kubernetes_workload_type."
            )
        return (
            f"No {destination} redirect is configured for {repo_name}. "
            "This app may be a library/package with no Rancher deployment."
        )

    if destination == "datadog":
        return (
            f"No Datadog redirect is configured for {repo_name}. "
            "This app may not publish logs as a deployable service."
        )

    if destination == "pipeline":
        return f"No pipeline redirect is configured for {repo_name}."

    if destination in ("api", "api-dev"):
        return (
            f"No {destination} redirect is configured for {repo_name}. "
            "This app may not expose an API endpoint."
        )

    return f"Unknown destination: {destination}"


def build_target(entry, destination):
    if destination == "datadog":
        return ("https://app.datadoghq.eu/logs?query=service%3A" +
                entry["datadog_service_name"])

    if destination in ("rancher", "rancher-dev"):
        subdomain = ("rancher.dev" if destination == "rancher-dev"
                     else "rancher")
        if entry["kubernetes_workload_type"] == "cronjob":
            resource_type = "batch.cronjob"
            fragment = "#jobs"
        else:
            resource_type = "apps.deployment"
            fragment = "#pods"
        return ("https://" + subdomain + "." + CLUSTER_DOMAIN + "/dashboard/"
                "c/local/explorer/" + resource_type + "/" +
                entry["rancher_namespace"] + "/" +
                entry_kubernetes_workload_name(entry) + fragment)

    if destination == "repo":
        return ("https://dev.azure.com/" +
                entry["azure_devops_org"] + "/" +
                entry["project_name"] + "/_git/" +
                entry["repo_name"])

    if destination == "pipeline":
        return ("https://dev.azure.com/" +
                entry["azure_devops_org"] + "/" +
                entry["project_name"] +
                "/_build?definitionId=" + entry["pipeline_id"])

    if destination in ("api", "api-dev"):
        api_hostname = entry["api_hostname"]
        subdomain = (api_hostname + ".dev"
                     if destination == "api-dev"
                     else api_hostname)
        return "https://" + subdomain + "." + CLUSTER_DOMAIN

    return None


by_datadog_service, by_rancher, by_repo, by_pipeline, by_api_hostname = load_config()


class Handler(BaseHTTPRequestHandler):
    def get_requester_identity(self):
        # Prefer an authenticated username if an upstream proxy forwards one.
        for header in (
            "X-Forwarded-User",
            "X-Auth-Request-User",
            "Remote-User",
            "X-Remote-User",
        ):
            value = self.headers.get(header)
            if value:
                return value

        for header in ("X-Forwarded-For", "X-Real-IP"):
            value = self.headers.get(header)
            if value:
                return value.split(",", 1)[0].strip()

        client_ip = self.client_address[0]
        try:
            hostname = socket.gethostbyaddr(client_ip)[0]
            if hostname and hostname != client_ip:
                return hostname
        except (socket.herror, socket.gaierror, OSError):
            pass

        return client_ip

    def log_redirect_failure(self, route, url, reason, message):
        event = {
            "user": self.get_requester_identity(),
            "result": "failure",
            "reason": reason,
            "message": message,
            "source": describe_source(url) if url else "Unknown",
            "destination": describe_destination(route),
            "url": url,
        }
        event.update(source_debug_fields(url))
        print(json.dumps(event), flush=True)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path.lstrip("/")
        params = parse_qs(parsed.query)
        url = params.get("url", [""])[0]

        if route == "health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        if route == "reload":
            global by_datadog_service, by_rancher, by_repo, by_pipeline, by_api_hostname
            (
                by_datadog_service,
                by_rancher,
                by_repo,
                by_pipeline,
                by_api_hostname,
            ) = load_config()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Config reloaded.")
            return

        if route not in {"datadog", "rancher", "rancher-dev", "repo", "pipeline", "api", "api-dev"}:
            message = "Unknown destination: " + route
            self.log_redirect_failure(route, url, "unknown_destination", message)
            self.send_error(400, message)
            return

        if not url:
            message = "Missing url parameter"
            self.log_redirect_failure(route, url, "missing_url", message)
            self.send_error(400, message)
            return

        entry = identify_app(url)
        if not entry:
            message = describe_mapping_failure(url)
            self.log_redirect_failure(route, url, "no_mapping", message)
            self.send_error(404, message)
            return

        if not destination_is_supported(entry, route):
            message = describe_unsupported_destination(entry, route)
            self.log_redirect_failure(route, url, "unsupported_destination", message)
            self.send_error(404, message)
            return

        target = build_target(entry, route)
        if not target:
            message = "Unknown destination: " + route
            self.log_redirect_failure(route, url, "unknown_destination", message)
            self.send_error(400, message)
            return

        requester = self.get_requester_identity()
        app_name = entry["repo_name"]
        source = describe_source(url)
        destination = describe_destination(route)
        print(json.dumps({
            "user": requester,
            "app": app_name,
            "source": source,
            "destination": destination,
        }), flush=True)
        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    host = "127.0.0.1"
    port = 1111
    print(f"Redirector running on http://{host}:{port}")
    print(f"Config: {CONFIG_PATH}")
    print(describe_azure_devops_pat_source())
    HTTPServer((host, port), Handler).serve_forever()
