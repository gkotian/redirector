#!/usr/bin/env python3

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

CONFIG_PATH = Path(__file__).parent / "config.json"
OLD_AZDO_HOST_SUFFIX = ".visualstudio.com"

# Maps Azure DevOps project names to their Rancher namespaces when creating a
# new project entry for a project not yet present in config.json.
PROJECT_NAMESPACE_MAP = {
}

# Maps Azure DevOps project names to repo-name prefixes that should be stripped
# before deriving the default api_hostname guess.
PROJECT_API_HOSTNAME_PREFIX_MAP = {
}


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def normalize_repo_url(repo_url):
    parsed = urlparse(repo_url)
    host = parsed.hostname or ""
    path_parts = [part for part in parsed.path.split("/") if part]

    if host.endswith(OLD_AZDO_HOST_SUFFIX):
        org = host[: -len(OLD_AZDO_HOST_SUFFIX)]
        if len(path_parts) >= 3 and path_parts[1] == "_git":
            project = path_parts[0]
            repo_name = path_parts[2]
            normalized = (
                f"https://dev.azure.com/{org}/{project}/_git/{repo_name}"
            )
            return normalized, org, project, repo_name

    if host == "dev.azure.com":
        if len(path_parts) >= 4 and path_parts[2] == "_git":
            org = path_parts[0]
            project = path_parts[1]
            repo_name = path_parts[3]
            normalized = (
                f"https://dev.azure.com/{org}/{project}/_git/{repo_name}"
            )
            return normalized, org, project, repo_name

    raise ValueError(
        "Unsupported Azure DevOps repository URL. Expected either "
        "https://{org}.visualstudio.com/{project}/_git/{repo} or "
        "https://dev.azure.com/{org}/{project}/_git/{repo}."
    )


def find_org_entry(config, org):
    for org_entry in config:
        if org_entry["azure_devops_org"] == org:
            return org_entry
    return None


def find_project_entry(org_entry, project_name):
    for project in org_entry["azure_devops_projects"]:
        if project["project_name"] == project_name:
            return project
    return None


def guess_namespace(org, project_name, existing_project):
    if existing_project:
        return existing_project["rancher_namespace"]
    return PROJECT_NAMESPACE_MAP.get(project_name, "")


def dashify_repo_name(repo_name):
    return re.sub(r"[^a-z0-9]+", "-", repo_name.lower()).strip("-")


def guess_api_hostname(project_name, repo_name):
    prefix = PROJECT_API_HOSTNAME_PREFIX_MAP.get(project_name)
    if prefix and repo_name.startswith(prefix):
        remainder = repo_name[len(prefix):]
        return re.sub(r"[^a-z0-9]+", "", remainder.lower())

    if "." in repo_name:
        return dashify_repo_name(repo_name)

    return ""


def prompt_yes_no(prompt, default=True):
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        answer = input(prompt + suffix).strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer y or n.")


def prompt_string(label, default):
    while True:
        prompt = f"{label}: " if not default else f"{label} [{default}]: "
        answer = input(prompt).strip()
        if answer:
            return answer
        if default:
            return default
        print(f"{label} is required.")


def prompt_optional_string(label, default):
    prompt = f"{label}: " if not default else f"{label} [{default}]: "
    answer = input(prompt).strip()
    return answer or default


def prompt_bool(label, default):
    rendered_default = "y" if default else "n"
    while True:
        answer = input(f"{label} (y/n) [{rendered_default}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer y or n.")


def prompt_choice(label, default, choices):
    choices_display = "/".join(choices)
    while True:
        prompt = f"{label} ({choices_display}) [{default}]: "
        answer = input(prompt).strip().lower()
        if not answer:
            return default
        if answer in choices:
            return answer
        print(f"Please answer with one of: {choices_display}.")


def format_field_value(value, not_applicable=False):
    if not_applicable:
        return "-N/A-"
    return value or "(blank)"


def print_entry(fields):
    print("")
    print("Proposed entry:")
    print(f"  repo_url: {fields['repo_url']}")
    print(f"  azure_devops_org: {fields['azure_devops_org']}")
    print(f"  project_name: {fields['project_name']}")
    print(f"  repo_name: {fields['repo_name']}")
    print(f"  pipeline_id: {format_field_value(fields['pipeline_id'])}")
    print(f"  visible_on_rancher: {fields['is_deployed']}")
    print(
        "  rancher_namespace: "
        f"{format_field_value(fields['rancher_namespace'], not fields['is_deployed'])}"
    )
    print(
        "  datadog_service_name: "
        f"{format_field_value(fields['datadog_service_name'], not fields['is_deployed'])}"
    )
    print(
        "  kubernetes_workload_name: "
        f"{format_field_value(fields['kubernetes_workload_name'], not fields['is_deployed'])}"
    )
    print(
        "  kubernetes_workload_type: "
        f"{format_field_value(fields['kubernetes_workload_type'], not fields['is_deployed'])}"
    )
    print(f"  exposes_api: {fields['has_api']}")
    print(
        "  api_hostname: "
        f"{format_field_value(fields['api_hostname'], not fields['has_api'])}"
    )


def normalize_fields(fields):
    if fields["is_deployed"]:
        if not fields["kubernetes_workload_type"]:
            fields["kubernetes_workload_type"] = "deployment"
        if not fields["kubernetes_workload_name"]:
            fields["kubernetes_workload_name"] = fields["datadog_service_name"]
        if fields["kubernetes_workload_type"] == "cronjob":
            fields["has_api"] = False
    else:
        fields["rancher_namespace"] = ""
        fields["datadog_service_name"] = ""
        fields["kubernetes_workload_name"] = ""
        fields["kubernetes_workload_type"] = ""
        fields["has_api"] = False

    if not fields["has_api"]:
        fields["api_hostname"] = ""

    return fields


def prompt_rancher_and_api_fields(
    fields, include_rancher_visibility, include_workload_type=False
):
    repo_name = fields["repo_name"]

    if include_rancher_visibility:
        fields["is_deployed"] = prompt_bool(
            f"Is {repo_name} visible on rancher?", fields["is_deployed"]
        )

    if fields["is_deployed"] and include_workload_type:
        fields["kubernetes_workload_type"] = prompt_choice(
            "kubernetes_workload_type",
            fields["kubernetes_workload_type"],
            ("deployment", "cronjob"),
        )
        if fields["kubernetes_workload_type"] == "cronjob":
            fields["has_api"] = False

    if fields["is_deployed"] and fields["kubernetes_workload_type"] != "cronjob":
        fields["has_api"] = prompt_bool(
            f"Does {repo_name} expose an API?", fields["has_api"]
        )

    if fields["has_api"]:
        fields["api_hostname"] = prompt_optional_string(
            "api_hostname", fields["api_hostname"]
        )

    return normalize_fields(fields)


def edit_fields(fields):
    print("")
    print("Edit values. Press Enter to keep the default shown in brackets.")
    fields["azure_devops_org"] = prompt_string(
        "azure_devops_org", fields["azure_devops_org"]
    )
    fields["project_name"] = prompt_string("project_name", fields["project_name"])
    fields["repo_name"] = prompt_string("repo_name", fields["repo_name"])

    if fields["is_deployed"]:
        fields["rancher_namespace"] = prompt_optional_string(
            "rancher_namespace", fields["rancher_namespace"]
        )
        fields["datadog_service_name"] = prompt_optional_string(
            "datadog_service_name", fields["datadog_service_name"]
        )
        fields["kubernetes_workload_name"] = prompt_optional_string(
            "kubernetes_workload_name", fields["kubernetes_workload_name"]
        )
        fields["kubernetes_workload_type"] = prompt_choice(
            "kubernetes_workload_type",
            fields["kubernetes_workload_type"],
            ("deployment", "cronjob"),
        )

    return prompt_rancher_and_api_fields(
        fields, include_rancher_visibility=False
    )


def collect_fields(initial_fields):
    fields = dict(initial_fields)
    fields["pipeline_id"] = prompt_optional_string(
        "Enter the pipeline definition ID", fields["pipeline_id"]
    )
    fields = prompt_rancher_and_api_fields(
        fields, include_rancher_visibility=True, include_workload_type=True
    )

    while True:
        print_entry(fields)
        if prompt_yes_no("Use these values?", default=True):
            return fields
        fields = edit_fields(fields)


def build_app_entry(fields):
    entry = {"repo_name": fields["repo_name"]}
    if fields["pipeline_id"]:
        entry["pipeline_id"] = fields["pipeline_id"]
    if fields["is_deployed"] and fields["datadog_service_name"]:
        entry["datadog_service_name"] = fields["datadog_service_name"]
    if (
        fields["is_deployed"]
        and fields["kubernetes_workload_name"]
        and fields["kubernetes_workload_name"] != fields["datadog_service_name"]
    ):
        entry["kubernetes_workload_name"] = fields["kubernetes_workload_name"]
    if fields["api_hostname"]:
        entry["api_hostname"] = fields["api_hostname"]
    if not fields["is_deployed"]:
        entry["is_deployed"] = False
    if fields["is_deployed"]:
        entry["kubernetes_workload_type"] = fields["kubernetes_workload_type"]
    return entry


def insert_entry(config, fields):
    org_entry = find_org_entry(config, fields["azure_devops_org"])
    if org_entry is None:
        org_entry = {
            "azure_devops_org": fields["azure_devops_org"],
            "azure_devops_projects": [],
        }
        config.append(org_entry)

    project_entry = find_project_entry(org_entry, fields["project_name"])
    if project_entry is None:
        project_entry = {
            "project_name": fields["project_name"],
            "rancher_namespace": fields["rancher_namespace"],
            "apps": [],
        }
        org_entry["azure_devops_projects"].append(project_entry)
    else:
        project_entry["rancher_namespace"] = fields["rancher_namespace"]

    for app in project_entry["apps"]:
        if app["repo_name"] == fields["repo_name"]:
            raise ValueError(
                "An entry with this repo_name already exists in the target project."
            )

    project_entry["apps"].append(build_app_entry(fields))
    project_entry["apps"].sort(key=lambda app: app["repo_name"].lower())
    org_entry["azure_devops_projects"].sort(
        key=lambda project: project["project_name"].lower()
    )


def main():
    parser = argparse.ArgumentParser(
        description="Interactively add an app entry to config.json."
    )
    parser.add_argument("repo_url", help="Azure DevOps repository URL")
    args = parser.parse_args()

    try:
        normalized_url, org, project_name, repo_name = normalize_repo_url(args.repo_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    config = load_config()
    org_entry = find_org_entry(config, org)
    project_entry = find_project_entry(org_entry, project_name) if org_entry else None

    fields = {
        "repo_url": normalized_url,
        "azure_devops_org": org,
        "project_name": project_name,
        "rancher_namespace": guess_namespace(org, project_name, project_entry),
        "repo_name": repo_name,
        "pipeline_id": "",
        "datadog_service_name": dashify_repo_name(repo_name),
        "kubernetes_workload_name": dashify_repo_name(repo_name),
        "api_hostname": guess_api_hostname(project_name, repo_name),
        "has_api": bool(guess_api_hostname(project_name, repo_name)),
        "is_deployed": True,
        "kubernetes_workload_type": "deployment",
    }

    fields = collect_fields(fields)

    if not prompt_yes_no("Create this entry in config.json?", default=True):
        print("Aborted.")
        return 0

    try:
        insert_entry(config, fields)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    save_config(config)
    print("")
    print("Entry added to config.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
