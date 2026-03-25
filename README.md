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
            "is_cronjob": false
          }
        ]
      }
    ]
  }
]
```

Per-app optional fields:
- `api_hostname` — omit if the app has no API endpoint
- `is_cronjob` — omit or set `false` for deployments, `true` for k8s cronjobs

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

## Running on startup

The server should ideally be always running. To start it automatically on
logging in:

**Windows:** Create a shortcut to `pythonw redirector.py` (note `pythonw`,
not `python`, to avoid a console window) and place it in your Startup folder
(`Win+R` > `shell:startup`).

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
