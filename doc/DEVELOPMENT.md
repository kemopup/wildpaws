# Local development on Windows

This checkout is a development copy of <https://github.com/kemopup/wildpaws>.
Production runs separately at `/home/redwolf/wildpaws` on the server. Local setup
does not deploy, connect to its database, or copy its secrets. The final section
describes the separate, explicit production deployment procedure.

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

## Post-link imports

The upload page can prepare drafts from e621, E6AI, Rule34, TBIB, Gelbooru, and X/Twitter post links.
It imports the original-file URL, source URLs, rating, description, and **only
artist and character tags**. It never copies general, species, copyright, or meta tags.
Importing previews metadata; it does not create a post until you upload it.
Source URLs, tags, and the description remain editable. Descriptions are converted
from common DText/BBCode formatting to Markdown. Formatting with no equivalent
is kept readable, with a warning to review simplified formatting. Rule34's API
may provide no description; comments are not substituted. E6AI's director/contributor tags are not artist tags and
are deliberately excluded.

Public e621/E6AI posts can work without an account. To access posts hidden
from guests, configure that provider's username and API key in the private
server configuration (`dev/config.local.yaml` in Dev, `server/config.yaml`
in production). Use API keys, never account passwords. These accounts are
shared by logged-in users with upload privileges; they do not bypass provider
deletion rules, account restrictions, or site access requirements. Keys are
used only by the server and are not returned to the browser.

```yaml
post_import:
    user_agent: 'Wildpaws/1.0 (by YOUR_USERNAME on e621)'
    e621:
        username: YOUR_USERNAME
        api_key: YOUR_API_KEY
    e6ai:
        username: YOUR_E6AI_USERNAME
        api_key: YOUR_E6AI_API_KEY
    rule34:
        user_id: YOUR_RULE34_USER_ID
        api_key: YOUR_RULE34_API_KEY
```

Use only providers you need; omit unused account blocks. Restart the API after
changing private configuration. Never commit the populated configuration or
paste keys into chat. Rule34 requires its own API credentials. Before enabling
it, review its [API terms](https://api.rule34.xxx/), including its rules against
ads/paywalls and multiple keys. E621/E6AI credentials do not work on Rule34.
Requests are serialized per provider and spaced at least 1.1 seconds apart in
each API process. Deployments with multiple API processes need a shared limiter
before enabling imports at scale.

Original-file downloads from supported provider domains use the same descriptive
application User-Agent as metadata requests when the general `user_agent` is
unset. A configured general `user_agent` still takes precedence for downloads.
Provider API credentials are never attached to original-file requests.
Gelbooru and Inkbunny queue thumbnails are requested separately from `/import-thumbnail`.
The server sends the provider's required download headers and returns a small JPEG
for display, since browser requests from the local site can receive an HTML
hotlink response. Thumbnail failure does not block uploading. The original file
URL remains the upload source, and the full-resolution file is not resized.
TBIB, Gelbooru, and Inkbunny downloads send the originating site's root as the Referer.

TBIB uses its XML post API and public post-page metadata; its legacy API ignores
batched tag queries. Gelbooru uses public post-page metadata when no account is
configured. Both read only explicitly categorized artist/character tags and the
"Original image" link, never the displayed sample or comments. Public HTML is
fetched as text; embedded media is not downloaded while preparing metadata.
If a provider misclassifies a tag, review and edit it in the draft.

Gelbooru can instead use its API when its own `post_import.gelbooru.user_id` and
`api_key` are configured. This uses categorized tag lookups and may require
credentials depending on the site's access policy. Rule34 credentials cannot
be reused for Gelbooru. Post links are normalized to remove search parameters
and any supplied credentials before requests and source storage.

Create local `artist` and `character` tag categories (case-insensitive names)
if they do not already exist. Only newly created tags receive these categories;
existing tags keep their categories. If a category is missing, the preview warns
that new tags will use the default category. Review all imported information.

Inkbunny imports use its [official API](https://wiki.inkbunny.net/wiki/API).
Add the following to the private server configuration for an account with
**Enable API Access** selected in its Inkbunny settings:

```yaml
post_import:
    inkbunny:
        username: 'YOUR_INKBUNNY_USERNAME'
        password: 'YOUR_INKBUNNY_PASSWORD'
```

Inkbunny requires an account password, unlike the API-key providers above.
Keep it only in the private YAML, never in Git or browser settings. Login and
session IDs are sent only to fixed Inkbunny HTTPS API endpoints, in POST bodies.
The server caches the API session and renews it once if it expires. Restart the
API after changing credentials. Registered-account rating and blocking settings
are respected. Leaving both fields blank uses a guest session; guest-blocked
submissions remain unavailable.

`https://inkbunny.net/s/123` imports all supported attached files in display order;
`https://inkbunny.net/s/123-p2-` selects one page. Legacy `submissionview.php?id=123`
links are normalized, and unrelated search arguments are removed. Each page
becomes an editable draft with the full original URL and a display-only thumbnail.
Inkbunny page thumbnails are loaded one at a time through the server, so its
hotlink protection does not break local browser previews or block uploading.
Only public original media locations are supported; private URLs requiring a
session token are rejected. Unsupported attachments produce a clear error;
use a supported page's link to import it separately. Inkbunny keywords have no
artist/character categories, so none are imported. The submission owner is
credited in the description without being assumed to be its artist. BBCode is
converted to Markdown, with a warning for simplified formatting. General,
Mature, and Adult map to `safe`, `sketchy`, and `unsafe` respectively.

Fur Affinity imports use a private helper with the pinned
[FAAPI library](https://github.com/FurryCoders/FAAPI). The helper has its own
Python runtime, so the main application stack stays unchanged. Local Compose
starts it as `fa-import`; do not publish its port or proxy it to the browser.
The Wildpaws API sends account cookies only to this fixed internal service.
It never sends them to a public third-party API mirror or a media CDN.

```yaml
post_import:
    furaffinity:
        a: 'YOUR_FA_A_COOKIE_VALUE'
        b: 'YOUR_FA_B_COOKIE_VALUE'
```

Use the values only, not a full Cookie header. Keep them in private YAML and
use the account's Modern theme. The cookies belong to an active FA login;
logging out of that originating session invalidates them. There is no promised
renewal schedule. Replace both from an active login if the importer reports
an expired/rejected session, then restart the Wildpaws API. Cookie changes
replace the helper's cached account session automatically on the next request.

`/view/123/` and `/full/123/` links normalize to the canonical HTTPS submission.
The draft retains the original media URL, source link, rating, uploader credit,
and Markdown description. FA keywords are uncategorized, so none are imported.
Display previews use the server thumbnail endpoint; originals remain untouched.
Deleted/private submissions, login failures, theme mismatches, and blocked or
rate-limited requests return readable errors without alternate access paths.

The helper enforces a shared minimum request interval and exponential cooldown
after rate limits/timeouts. Review FA's current
[automated-access policy and API announcement](https://www.furaffinity.net/journal/11377368/)
before operating the integration: it describes access requests for
machine-to-machine scraping services and restrictions during high traffic or
DDoS mitigation. An official API closed beta is a future integration option.

Bluesky imports use the [official public API](https://bsky.network/docs/api-directory/)
directly. No Bluesky password, app password, session cookie, or API key is
needed for public-post imports. Both Bluesky and FxBsky share links are accepted;
queries are removed and the canonical source uses the author's stable DID.
An optional `#media-N` selector imports just that attached item.

Post text is converted to literal Markdown with uploader credit and rich-text
links. Artist/character/hashtag tags are not inferred. Each attached image or
video becomes a separate editable draft, in order. Quoted posts, external card
images, replies, and profile media are not imported. Bluesky labels do not map
directly to the site's three ratings, so drafts use `unsafe` and require rating
review. Deleted, unavailable, and unauthenticated-view-restricted posts return
an error; the importer does not try another record access path.

Original media comes from the author's verified data server using
[`com.atproto.sync.getBlob`](https://atproto.com/specs/blob). These are the full
stored files, which Bluesky may already have processed before storing. The
uploader's DID is resolved and checked, public HTTPS destinations are validated,
connections are pinned to checked public addresses, redirects are rejected, and
downloaded bytes are checked against their content hash. Existing upload size
limits still apply. Display-only image/video previews come from validated
Bluesky CDNs through the separate thumbnail endpoint; they never replace the
original file. No other provider's credentials are sent to Bluesky or its data
servers.

X/Twitter imports use the public [FxTwitter API](https://github.com/allnodes/FxTwitter)
at `api.fxtwitter.com`; no X login or API key is sent. Only the post ID is sent
to that service. The post author's username is credited with a profile link in
the Markdown description, followed by the escaped post text. The author is not
assumed to be the artist: X imports create no artist, character, mention, or
hashtag tags automatically.

Each attached photo or video becomes its own editable draft, in post order.
Plain `http://x.com` and `http://twitter.com` links are normalized to HTTPS;
all API and media requests use HTTPS.
`/photo/N` and `/video/N` links select only the requested item. Photo URLs request
X's `name=orig` size; videos use the best available supported published MP4
variant (or a direct WebM). X animated GIFs are imported as looping MP4s. Video
posters are thumbnails only and never replace the actual video file URL. Quoted
post media, external card embeds, mosaics, avatars, and profile biographies are
excluded. Protected or unavailable posts return an error without trying another
access path. X's sensitivity flag is not a compatible three-level rating, so
these drafts default to `unsafe` and ask the uploader to review the rating.

Provider references: [e621 serializer](https://github.com/e621ng/e621ng/blob/master/app/blueprints/post_legacy_blueprint.rb),
[E6AI API](https://e6ai.net/help/api), [Rule34 API](https://api.rule34.xxx/).

## Publishing code changes

The agreed workflow is:

1. Edit and verify the change in this local development environment.
2. Commit and push the verified code to `kemopup/wildpaws` on GitHub.
3. Update `/home/redwolf/wildpaws` on the server from GitHub, then rebuild and
   restart the affected Docker Compose services.

Server updates must preserve its existing local Compose changes and application
configuration. The development Compose file and credentials are only for local
use. Development setup by itself does not change the production app.
The inherited DockerHub publishing jobs run only in `rr-/szurubooru`; this fork
builds its own production images locally from the reviewed GitHub commit.

### Production deployment

Use the reviewed `dev/deploy-production.sh` after the verified code is available
on GitHub `master`. Run it as `redwolf`, the repository owner, **not** with
`sudo bash`. The script requests sudo for individual Docker commands if needed
and refreshes it immediately before maintenance. Keep the copied script outside
the checkout until it updates the repository. Replace `FULL_GITHUB_MASTER_SHA`
below with the reviewed, full 40-character commit SHA; it must match
`origin/master` exactly. An exclusive repository lock prevents concurrent apply
runs.

```sh
bash /home/redwolf/deploy-production.sh --commit FULL_GITHUB_MASTER_SHA --plan --post-edit-policy
bash /home/redwolf/deploy-production.sh --commit FULL_GITHUB_MASTER_SHA --apply --post-edit-policy
```

The plan makes no changes. Apply preserves the original branch pointer (including
`safe-backup`) by creating a new `codex/production-<timestamp>` branch and
fast-forwarding it to the reviewed commit. It preserves the existing locally
modified `docker-compose.yml`, `.env`, private `server/config.yaml`, and untracked
files such as the old virtual environment. Tracked local changes other than the
base Compose file, or an incoming change to that customized file, stop deployment
for explicit resolution. There is no force checkout or stash.

Production must use both Compose files:

```sh
docker compose -f docker-compose.yml -f compose.importers.yaml ps
```

The `compose.importers.yaml` overlay retains the installation's client/server
builds, ports, environment, data mounts and SQL configuration. It adds the
private FA helper without published ports, waits for its local health check, and
sets client/server/helper restart policies to `unless-stopped`. Always include
this overlay for subsequent app updates; the development Compose file is not
used on the server.

Before any source or service update, the script requires existing client/server
builds to resolve to this checkout's `client` and `server` directories, with
their default Dockerfiles and either the `release` target or no explicit target.
Image-only configurations, other build directories, custom Dockerfiles and test
targets require explicit resolution instead of deploying a different image.
It also verifies that both resolved Compose settings and the running containers
use the local `sql` service on port 5432 with matching nonblank database names
and roles. The name is `POSTGRES_DB` when present, otherwise `POSTGRES_USER`.
Any disagreement stops deployment before source or service changes, so the
backup cannot silently target a different database. Environment values and
passwords are never printed during these checks.

The script builds all new app images before stopping the running client/server.
By default, it then creates a consistent PostgreSQL custom-format dump and a
complete original-media tar archive while the app is stopped; SQL stays running.
It also backs up private YAML, `.env`, Compose files, the local Compose patch,
and previous running image references outside the repository under
`/home/redwolf/wildpaws-backups`. Backup paths must not overlap data/SQL mounts or
the repository, and available space is checked. The directory contains secrets:
keep it private. `--backup-root /absolute/separate/path` selects another location;
use `--skip-media-backup` only when a separate media backup already exists.

Existing production provider accounts remain in place. The script never copies
Dev account credentials or resets unrelated settings. The explicit
`--post-edit-policy` flag changes only these requested ranks: content, thumbnail,
safety, source, relations, flags and general description editing require
`moderator`; tags, notes and editing one's own description require `regular`.
All other privilege entries, including the general post-edit access setting,
remain unchanged. This update is atomic and retains the file's owner and mode.

Startup recreates the client/server containers even on a repeated deployment,
so their bind mounts use the newly written private config file. Deployment
finishes by checking the local client and JSON API. If a backup fails
before the new server starts, it attempts to restart the unchanged old containers.
Once new startup/database migrations begin, rollback is manual: the private
backup includes `ROLLBACK.txt` and an override referencing the saved old images.
Confirm schema compatibility before using old images. No database restore,
database/media deletion, or volume removal happens automatically.
