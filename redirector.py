import json
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import SplitResult
from urllib.parse import parse_qs, unquote, urlparse

CONFIG_PATH = Path(__file__).parent / "config.json"
CLUSTER_DOMAIN = "example.com"


def entry_kubernetes_workload_name(entry):
    return entry.get("kubernetes_workload_name") or entry.get("datadog_service_name")


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
        path = parsed.path.rstrip("/").split("/")
        service = path[-1].split("#")[0]
        namespace = path[-2]
        return by_rancher.get((namespace, service))

    if "datadoghq" in host:
        query = parse_qs(parsed.query)
        q = query.get("query", [""])[0]
        for part in q.split(" "):
            if part.startswith("service:"):
                service = part.split(":", 1)[1]
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
                pipeline_id = query.get("definitionId", [""])[0]
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

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path.lstrip("/")
        params = parse_qs(parsed.query)
        url = unquote(params.get("url", [""])[0])

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

        if not url:
            self.send_error(400, "Missing url parameter")
            return

        entry = identify_app(url)
        if not entry:
            self.send_error(404, "No mapping found for this URL")
            return

        if route not in {"datadog", "rancher", "rancher-dev", "repo", "pipeline", "api", "api-dev"}:
            self.send_error(400, "Unknown destination: " + route)
            return

        if not destination_is_supported(entry, route):
            self.send_error(404, describe_unsupported_destination(entry, route))
            return

        target = build_target(entry, route)
        if not target:
            self.send_error(400, "Unknown destination: " + route)
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
        }))
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
    HTTPServer((host, port), Handler).serve_forever()
