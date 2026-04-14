# Redirector

A local HTTP server that lets you jump between Rancher, Datadog, and Azure
DevOps pages for the same app using browser bookmarklets.

## Config

Edit `config.json` to add apps. Structure:

```json
[
  {
    "azure_devops_org": "my-org",
    "azure_devops_projects": [
      {
        "project_name": "my.project",
        "rancher_namespace": "my-namespace",
        "apps": [
          {
            "repo_name": "My.App",
            "pipeline_id": "123",
            "datadog_service_name": "my-app",
            "api_hostname": "myapp",
            "kubernetes_workload_type": "deployment"
          }
        ]
      }
    ]
  }
]
```

Per-app optional fields:
- `pipeline_id` — omit if there is no Azure DevOps pipeline redirect
- `is_deployed` — set to `false` if the app is not deployed and has no Rancher/Datadog redirects
- `datadog_service_name` — omit if `is_deployed` is `false`
- `api_hostname` — omit if the app has no API endpoint
- `kubernetes_workload_type` — for deployed apps, set to `deployment` or `cronjob`

Project-level note:
- `rancher_namespace` can be left blank for projects with no Rancher deployment

After editing, reload the config by visiting
`http://localhost:1111/reload` in your browser, or restart the server.

To add entries interactively, run:

```bash
python add_config_entry.py \
  'https://dev.azure.com/my-org/my.project/_git/My.App'
```

The helper also accepts old-style Azure DevOps URLs such as
`https://my-org.visualstudio.com/my.project/_git/My.App`, normalizes them,
shows guessed defaults, and inserts the new entry with projects sorted by
`project_name` and apps sorted by `repo_name`.

To process queued URLs from `urls/all-urls.txt`, run:

```bash
python add_config_entry_helper.py
```

The helper keeps showing the next topmost URL until the file is empty. Enter
`q` to stop without consuming the current URL.

## Running on startup

The server should ideally be always running. To start it automatically on
logging in:

**Windows:** Copy `StartRedirector.vbs` from this repo into your Startup
folder (`Win+R` > `shell:startup`). The script starts `redirector.py` via
`pythonw` so no console window is shown, and it first terminates any older
redirector instance that was started from the same script path. If you change
`StartRedirector.vbs`, copy the updated file into the Startup folder again.

**Linux/macOS:** Add `python /path/to/redirector.py &` to your shell profile,
or create a systemd service / launchd plist.

## Bookmarklets

Add these to your browser's bookmark bar (right-click bar > Add page, paste
as URL):

**→ Datadog:**
```
javascript:void(window.open('http://localhost:1111/datadog?url='+encodeURIComponent(location.href)))
```

**→ Rancher (prod):**
```
javascript:void(window.open('http://localhost:1111/rancher?url='+encodeURIComponent(location.href)))
```

**→ Rancher (dev):**
```
javascript:void(window.open('http://localhost:1111/rancher-dev?url='+encodeURIComponent(location.href)))
```

**→ Repo:**
```
javascript:void(window.open('http://localhost:1111/repo?url='+encodeURIComponent(location.href)))
```

**→ Pipeline:**
```
javascript:void(window.open('http://localhost:1111/pipeline?url='+encodeURIComponent(location.href)))
```

**→ API (prod):**
```
javascript:void(window.open('http://localhost:1111/api?url='+encodeURIComponent(location.href)))
```

**→ API (dev):**
```
javascript:void(window.open('http://localhost:1111/api-dev?url='+encodeURIComponent(location.href)))
```

## Supported source pages

Each bookmarklet works from any of these pages:

- Rancher (prod or dev)
- Datadog logs
- Azure DevOps repo or pipeline
- API endpoint (prod or dev)
