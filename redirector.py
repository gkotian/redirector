import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

CONFIG_PATH = Path(__file__).parent / "config.json"
PORT = 1111
CLUSTER_DOMAIN = "example.com"


def load_config():
    with open(CONFIG_PATH) as f:
        orgs = json.load(f)

    by_datadog_service = {}
    by_rancher = {}
    by_repo = {}
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
                    "pipeline_id": app["pipeline_id"],
                    "datadog_service_name": app["datadog_service_name"],
                    "api_hostname": app.get("api_hostname"),
                    "is_cronjob": app.get("is_cronjob", False),
                }
                by_datadog_service[app["datadog_service_name"]] = entry
                by_rancher[
                    (namespace, app["datadog_service_name"])
                ] = entry
                by_repo[
                    (org, project_name, app["repo_name"])
                ] = entry
                if app.get("api_hostname"):
                    by_api_hostname[app["api_hostname"]] = entry

    return by_datadog_service, by_rancher, by_repo, by_api_hostname


def identify_app(url):
    parsed = urlparse(url)
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
            repo = path[3] if len(path) > 3 else None
            if repo:
                return by_repo.get((org, project, repo))
            for entry in by_datadog_service.values():
                if (entry["azure_devops_org"] == org and
                        entry["project_name"] == project):
                    return entry

    return None


def build_target(entry, destination):
    if destination == "datadog":
        return ("https://app.datadoghq.eu/logs?query=service%3A" +
                entry["datadog_service_name"])

    if destination in ("rancher", "rancher-dev"):
        subdomain = ("rancher.dev" if destination == "rancher-dev"
                     else "rancher")
        if entry["is_cronjob"]:
            resource_type = "batch.cronjob"
            fragment = "#jobs"
        else:
            resource_type = "apps.deployment"
            fragment = "#pods"
        return ("https://" + subdomain + "." + CLUSTER_DOMAIN + "/dashboard/"
                "c/local/explorer/" + resource_type + "/" +
                entry["rancher_namespace"] + "/" +
                entry["datadog_service_name"] + fragment)

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
        api_hostname = entry.get("api_hostname")
        if not api_hostname:
            return None
        subdomain = (api_hostname + ".dev"
                     if destination == "api-dev"
                     else api_hostname)
        return "https://" + subdomain + "." + CLUSTER_DOMAIN

    return None


by_datadog_service, by_rancher, by_repo, by_api_hostname = load_config()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path.lstrip("/")
        params = parse_qs(parsed.query)
        url = unquote(params.get("url", [""])[0])

        if route == "reload":
            global by_datadog_service, by_rancher, by_repo
            by_datadog_service, by_rancher, by_repo, by_api_hostname = load_config()
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

        target = build_target(entry, route)
        if not target:
            self.send_error(400, "Unknown destination: " + route)
            return

        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print(f"Navigation server running on http://localhost:{PORT}")
    print(f"Config: {CONFIG_PATH}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
