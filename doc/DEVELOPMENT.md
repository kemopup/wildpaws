# Local development on Windows

This checkout is a development copy of <https://github.com/kemopup/wildpaws>.
Production runs separately at `/home/redwolf/wildpaws` on the server. Nothing in
this workflow deploys, connects to the production database, or copies its secrets.

## Windows prerequisites

Install WSL 2 and Docker Desktop with the WSL 2 backend. If WSL is not installed,
open **PowerShell as Administrator** and run:

```powershell
wsl --install --no-distribution
```

Restart Windows if requested. Docker Desktop does not need an Ubuntu distribution
for this workflow. Install/start Docker Desktop and select Linux containers.
This project requires Docker Compose 2.32 or newer (included in current Desktop).

The CLI installer can be run from a normal PowerShell after that restart:

```powershell
winget install --id Docker.DockerDesktop --exact --source winget --override "install --user --accept-license --backend=wsl-2"
```

If the installer reports that Virtual Machine Platform is disabled, finish the
administrator WSL step above, restart, and retry the Docker installation.

Official setup instructions:
- <https://learn.microsoft.com/en-us/windows/wsl/install>
- <https://docs.docker.com/desktop/setup/install/windows-install/>

## First start

Open PowerShell in this repository:

```powershell
.\dev.cmd setup
.\dev.cmd doctor
.\dev.cmd check
.\dev.cmd watch
```

The `dev.cmd` launcher runs the PowerShell helper without changing your machine's
execution policy. You can also call `dev.ps1` directly if your policy allows it.

Open <http://localhost:8088>. The first build downloads dependencies and can take
several minutes. Register a **new local account**; the application's first account
in an empty database becomes an administrator. Production accounts are absent.

The script creates ignored `dev/.env` and `dev/config.local.yaml` files with random
development credentials. Running setup again preserves them. Edit the site name
or local settings in `dev/config.local.yaml`. If you change `DEV_HTTP_PORT` in
`dev/.env`, update `domain` in `dev/config.local.yaml` too.

## Editing and testing

| Work | Location / command |
| --- | --- |
| Templates, styles, browser JavaScript | `client/html`, `client/css`, `client/js` |
| Python API and application logic | `server/szurubooru` |
| Watch and rebuild while editing | `.\dev.cmd watch` |
| Start in background | `.\dev.cmd up` |
| View logs / container status | `.\dev.cmd logs` / `.\dev.cmd status` |
| Run existing Python test suite | `.\dev.cmd test` |
| Validate Compose without starting containers | `.\dev.cmd check` |
| Stop the development stack, preserving data | `.\dev.cmd down` |

The launcher rebuilds from source before enabling Compose Watch, so edits made
while watch was stopped are included. Watch then copies new edits into Linux
containers; it does not copy a second snapshot of host-generated build files at
startup. The frontend's existing build
watcher rebuilds HTML templates, Stylus and JavaScript, and refreshes the browser
through its existing WebSocket on `localhost:8080`. Nginx serves the app at port
8088 and proxies `/api/` to the local API. Python edits restart the API, including
running database migrations. Restart watch after changing API configuration,
`client/build.js`, `client/html/index.htm`, fonts, or Nginx configuration so these
are fully reapplied. Adding or deleting frontend source files may also require
restarting watch; the legacy watcher primarily handles changes to existing files.

`up` runs a snapshot of your code. Use `watch` for continuous updates, or rerun
`up` after edits to rebuild. Changes to package manifests rebuild the asset image.
The Python test image uses the repository's `testing` Dockerfile target, which
runs PostgreSQL in the disposable test container. It does not mount development
data. Frontend validation is a successful asset build plus a browser check.

After starting, an HTTP smoke check from PowerShell is:

```powershell
(Invoke-WebRequest http://localhost:8088 -UseBasicParsing).StatusCode
(Invoke-WebRequest http://localhost:8088/api/info -Headers @{Accept='application/json'} -UseBasicParsing).StatusCode
```

Both should return `200`.

## Data, ports, and production parity

The Compose project is always `wildpaws-dev`. Separate Docker named volumes hold
the development database (`dev-sql`), uploads (`dev-data`) and built assets
(`dev-assets`). The database and API have no host ports; web and live reload bind
only to `127.0.0.1`. No database dump or production uploads are imported.

The wrapper explicitly selects `compose.dev.yaml` and `dev/.env`, and refuses
remote Docker endpoints. It never uses the production `docker-compose.yml` or
root `.env`. `down` preserves named volumes; deleting volumes would erase local
test accounts and uploads.

The development API and test runner use the existing repository Dockerfile and
PostgreSQL 11 to match the deployed Compose stack. These are legacy dependencies;
upgrading them is a separate application change. The frontend dev image uses
Node 24 and the checked-in npm lockfile. Docker Compose Watch reference:
<https://docs.docker.com/compose/how-tos/file-watch/>.

The server checkout inspected during setup was commit `781c3c6` and had a local
Compose change replacing the upstream client/server images with source builds.
That checkout was left untouched. This local stack builds both from source too.

The current Windows checkout is under OneDrive. Docker volumes keep dependency
trees and database files outside OneDrive. If file watching or syncing is slow,
move the checkout to a non-synced directory such as `C:\dev\wildpaws` and use the
same commands there. Do not copy `node_modules` between operating systems.

## Publishing code changes

The agreed workflow is:

1. Edit and verify the change in this local development environment.
2. Commit and push the verified code to `kemopup/wildpaws` on GitHub.
3. Update `/home/redwolf/wildpaws` on the server from GitHub, then rebuild and
   restart the affected Docker Compose services.

Server updates must preserve its existing local Compose changes and application
configuration. The development Compose file and credentials are only for local
use. Development setup by itself does not change the production app.
