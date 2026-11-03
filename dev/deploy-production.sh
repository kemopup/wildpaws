#!/usr/bin/env bash
# Run as the repository owner. Only Docker commands use sudo when necessary.
# Plan:  bash /path/to/deploy-production.sh --commit FULL_GITHUB_MASTER_SHA
# Apply: bash /path/to/deploy-production.sh --commit FULL_GITHUB_MASTER_SHA --apply --post-edit-policy
# Backups contain secrets: keep the backup directory private and off public mounts.
set -Eeuo pipefail
set +x
umask 077

repo=/home/redwolf/wildpaws
backup_root=/home/redwolf/wildpaws-backups
commit=
apply=0
media_backup=1
post_edit_policy=0
backup=
phase=preflight
stopped=0
new_started=0
declare -a docker_cmd=(docker)

usage() {
    cat <<'USAGE'
Usage: deploy-production.sh --commit FULL_SHA [--apply] [--post-edit-policy]
       [--repo ABSOLUTE_PATH] [--backup-root ABSOLUTE_PATH] [--skip-media-backup]

Default is a read-only local plan. --apply fetches origin/master, requires its
exact SHA, fast-forwards a new deployment branch, and builds before stopping the app.
It preserves local docker-compose.yml, .env, server/config.yaml and untracked
files and the original branch pointer. Only docker-compose.yml may have tracked
local changes.

Backups include PostgreSQL, private configuration, Compose files, previous image
references and (by default) all media. SQL stays running. No database restore or
data deletion is automatic. Existing provider accounts are preserved; Dev
credentials are never copied. --skip-media-backup requires a separate media
backup and still creates the database/configuration backup.

--post-edit-policy changes only the explicit post-edit ranks requested for the
site: tags/notes regular; other fields moderator; uploader description regular.
All other configuration and privilege entries are preserved.

Run as the repository owner, not with sudo before bash. The script requests sudo
for Docker if needed; it never changes Docker/sudo group membership.
USAGE
}
die() { printf 'Deployment stopped: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case "$1" in
        --commit|--repo|--backup-root)
            (($# >= 2)) || die "Missing value for $1."
            case "$1" in
                --commit) commit=$2;; --repo) repo=$2;; --backup-root) backup_root=$2;;
            esac
            shift 2;;
        --apply) apply=1; shift;;
        --plan) apply=0; shift;;
        --skip-media-backup) media_backup=0; shift;;
        --post-edit-policy) post_edit_policy=1; shift;;
        -h|--help) usage; exit 0;;
        *) die "Unknown option; use --help.";;
    esac
done
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || die 'Provide the full lowercase 40-character GitHub master SHA.'
((EUID != 0)) || die 'Run as the repository owner; the script uses sudo only for Docker.'
for tool in git python3 readlink df awk mktemp; do command -v "$tool" >/dev/null || die "Required command unavailable: $tool."; done
[[ "$repo" = /* && "$backup_root" = /* ]] || die 'Repository and backup paths must be absolute.'
[[ "$backup_root" != *,* && "$backup_root" != *$'\n'* ]] || die 'Backup path cannot contain a comma or newline.'
repo=$(readlink -e -- "$repo") || die 'Repository directory does not exist.'
backup_root=$(readlink -m -- "$backup_root")
[[ "$backup_root" != / && "$backup_root" != "$repo" && "$backup_root" != "$repo/"* && "$repo" != "$backup_root/"* ]] || die 'Backup and repository directories must not overlap.'
cd -- "$repo"
[[ "$(git rev-parse --show-toplevel)" = "$repo" ]] || die 'The path must be the Git repository root.'
if ((apply == 1)); then
    command -v flock >/dev/null || die 'flock is required to prevent concurrent deployments.'
    exec 9>"$(git rev-parse --git-path wildpaws-deploy.lock)"
    flock -n 9 || die 'Another deployment is already active for this repository.'
fi
[[ -f docker-compose.yml && -f .env && -f server/config.yaml ]] || die 'The existing Compose file, .env and server/config.yaml are required.'
[[ ! -e .git/MERGE_HEAD && ! -d .git/rebase-merge && ! -d .git/rebase-apply ]] || die 'Finish the existing Git operation first.'
branch=$(git symbolic-ref --short -q HEAD) || die 'The repository must be on its existing branch.'
old_head=$(git rev-parse HEAD)
git diff --cached --quiet || die 'Commit or unstage the staged changes before deploying.'
while IFS= read -r path; do
    [[ -z "$path" || "$path" = docker-compose.yml ]] || die 'Tracked local changes other than docker-compose.yml must be resolved first.'
done < <(git diff --name-only)
python3 - "$repo/server/config.yaml" "$post_edit_policy" <<'PY'
import os, stat, sys
import yaml
path, update = sys.argv[1], sys.argv[2] == '1'
info = os.lstat(path)
if not stat.S_ISREG(info.st_mode):
    raise SystemExit('Production config must be a regular file.')
try:
    with open(path, encoding='utf-8') as stream:
        config = yaml.safe_load(stream)
except yaml.YAMLError:
    raise SystemExit('Production config is not valid YAML; no configuration values were printed.')
if not isinstance(config, dict):
    raise SystemExit('Production config must be a YAML mapping.')
if update:
    if info.st_uid != os.geteuid() or not os.access(path, os.W_OK) or not os.access(os.path.dirname(path), os.W_OK):
        raise SystemExit('Post-edit policy requires a config file and parent directory writable by the repository owner.')
    if 'privileges' in config and not isinstance(config['privileges'], dict):
        raise SystemExit('Production privileges must be a YAML mapping.')
PY

printf 'Plan: preserve branch %s at %s; fast-forward a new codex/production branch to %s.\n' "$branch" "$old_head" "$commit"
printf 'Preserve existing Compose builds, mounts, ports, provider accounts and untracked files.\n'
printf 'Build client/server/private FA helper while the current site serves traffic.\n'
printf 'Stop only client/server; back up SQL and configuration outside the repository.\n'
((media_backup == 0)) || printf 'Archive original media during the same maintenance window.\n'
((post_edit_policy == 0)) || printf 'Apply the requested post-edit ranks; preserve all other private YAML values.\n'
printf 'Start the new app, verify API and client, retain previous images for manual rollback.\n'
if ((apply == 0)); then
    printf 'Plan only: no source, containers, configuration or backups changed. Add --apply to execute.\n'
    exit 0
fi

command -v docker >/dev/null || die 'Docker is unavailable.'
if ! docker info >/dev/null 2>&1; then
    command -v sudo >/dev/null || die 'Docker requires privileges and sudo is unavailable.'
    sudo -v || die 'Docker administration requires sudo in this terminal.'
    # A long media backup can outlast sudo's timestamp; allow a fresh terminal
    # prompt instead of stranding the site offline when the cache expires.
    docker_cmd=(sudo docker)
fi
"${docker_cmd[@]}" info >/dev/null 2>&1 || die 'Docker administration is unavailable.'
"${docker_cmd[@]}" compose version >/dev/null 2>&1 || die 'Docker Compose v2 is required.'
declare -a base=("${docker_cmd[@]}" compose -f "$repo/docker-compose.yml")
declare -a compose=("${base[@]}" -f "$repo/compose.importers.yaml")
runtime_dir=$(mktemp -d)
cleanup_preflight() {
    rm -f -- "$runtime_dir/compose.before.json" "$runtime_dir/runtime.json" "$runtime_dir/preflight.log"
    rmdir -- "$runtime_dir" 2>/dev/null || true
}
trap cleanup_preflight EXIT
# Resolve privately in a temporary directory first: never place credentials in a
# requested backup location until it is proved outside the public data mounts.
"${base[@]}" config --format json >"$runtime_dir/compose.before.json" 2>"$runtime_dir/preflight.log" || die 'Cannot resolve the existing Compose configuration; no source or services changed.'
python3 - "$runtime_dir/compose.before.json" "$backup_root" "$runtime_dir/runtime.json" "$repo" <<'PY'
import json, os, sys
config = json.load(open(sys.argv[1], encoding='utf-8'))
services = config['services']
for service in ('server', 'client'):
    build = services[service].get('build')
    expected = os.path.join(sys.argv[4], service)
    if not isinstance(build, dict) or not isinstance(build.get('context'), str) or os.path.realpath(build['context']) != expected:
        raise SystemExit('Production client/server must build from their reviewed repository directories.')
    dockerfile = build.get('dockerfile', 'Dockerfile')
    if not isinstance(dockerfile, str) or os.path.realpath(os.path.join(expected, dockerfile)) != os.path.join(expected, 'Dockerfile') or 'dockerfile_inline' in build:
        raise SystemExit('Production client/server must use their default Dockerfile.')
    if build.get('target') not in (None, 'release'):
        raise SystemExit('Production client/server build target must be release or omitted.')
def mount(service, target):
    matches = [v for v in services[service].get('volumes', []) if v.get('target') == target]
    if len(matches) != 1 or matches[0].get('type') != 'bind':
        raise SystemExit('Expected one existing bind mount for application data and SQL.')
    source = os.path.realpath(matches[0]['source'])
    if not os.path.isabs(source) or source == '/' or not os.path.isdir(source) or '\n' in source or ',' in source:
        raise SystemExit('Existing data mount is not a valid absolute directory.')
    return source
data, sql = mount('server', '/data'), mount('sql', '/var/lib/postgresql/data')
backup = os.path.realpath(sys.argv[2])
def inside(a, b): return os.path.commonpath([a, b]) == b
if any(inside(a, b) for a, b in ((backup, data), (data, backup), (backup, sql), (sql, backup), (data, sql), (sql, data))):
    raise SystemExit('Media, SQL and backup directories must not overlap.')
if mount('client', '/data') != data:
    raise SystemExit('Client/server data mounts differ; review the installation first.')
ports = [p for p in services['client'].get('ports', []) if p.get('target') == 80 and p.get('protocol', 'tcp') == 'tcp']
if len(ports) != 1 or not str(ports[0].get('published', '')).isdigit():
    raise SystemExit('Expected one fixed client HTTP port.')
port = int(ports[0]['published'])
if not 1 <= port <= 65535:
    raise SystemExit('Invalid client port.')
with open(sys.argv[3], 'w', encoding='utf-8') as out:
    json.dump({'data': data, 'port': port}, out)
PY
data=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"])' "$runtime_dir/runtime.json")
port=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["port"])' "$runtime_dir/runtime.json")
mkdir -p -m 700 -- "$backup_root"
[[ -O "$backup_root" && ! -L "$backup_root" ]] || die 'Backup root must be owned by the repository owner and not be a symlink.'
chmod 700 -- "$backup_root"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$backup_root/$stamp-${commit:0:12}"
mkdir -m 700 -- "$backup"
mv -- "$runtime_dir/compose.before.json" "$runtime_dir/runtime.json" "$backup/"
mv -- "$runtime_dir/preflight.log" "$backup/deployment.log"
cleanup_preflight
log="$backup/deployment.log"
private() { "$@" >>"$log" 2>&1; }
env_temp=
cleanup_env() {
    if [[ -n "$env_temp" ]]; then
        rm -f -- "$env_temp/server.json" "$env_temp/sql.json"
        rmdir -- "$env_temp" 2>/dev/null || true
    fi
}
on_exit() {
    local code=$?
    cleanup_env
    if ((code != 0)); then
        printf 'Deployment failed during %s. Private backup/log: %s\n' "$phase" "$backup" >&2
        if ((stopped == 1 && new_started == 0)); then
            printf 'Restarting the unchanged old containers; new database migrations have not started.\n' >&2
            "${base[@]}" start server client >>"$log" 2>&1 || printf 'Old-container restart also failed; inspect the private log.\n' >&2
        fi
        printf 'No database restore was attempted. Read ROLLBACK.txt before changing images.\n' >&2
    fi
}
trap on_exit EXIT
phase='configuration and image backup'
mkdir -m 700 -- "$backup/files"
cp -p -- docker-compose.yml .env "$backup/files/"
cp -p -- server/config.yaml "$backup/files/server.config.yaml"
[[ ! -f compose.importers.yaml ]] || cp -p -- compose.importers.yaml "$backup/files/"
printf '%s\n' "$old_head" >"$backup/previous-commit.txt"
printf '%s\n' "$branch" >"$backup/previous-branch.txt"
git status --short >"$backup/git-status.txt"
git diff --binary -- docker-compose.yml >"$backup/local-compose.patch"
env_temp=$(mktemp -d "$backup/.running-env.XXXXXXXX")
for service in server client sql; do
    container=$("${base[@]}" ps -q "$service" 2>>"$log")
    [[ -n "$container" && "$container" != *$'\n'* ]] || die 'Expected one running container per service before deployment.'
    [[ "$("${docker_cmd[@]}" inspect --format '{{.State.Running}}' "$container" 2>>"$log")" = true ]] || die 'All three existing services must be running.'
    image=$("${docker_cmd[@]}" inspect --format '{{.Image}}' "$container" 2>>"$log")
    printf '%s %s %s\n' "$service" "$container" "$image" >>"$backup/previous-images.txt"
    if [[ "$service" = server || "$service" = sql ]]; then
        "${docker_cmd[@]}" inspect --format '{{json .Config.Env}}' "$container" >"$env_temp/$service.json" 2>>"$log"
    fi
    case "$service" in
        sql) sql_container=$container; sql_image=$image;;
        *) rollback_tag="wildpaws-rollback-$service:$stamp"
           private "${docker_cmd[@]}" image tag "$image" "$rollback_tag"
           printf '  %s:\n    image: %s\n' "$service" "$rollback_tag" >>"$backup/rollback.services.yaml";;
    esac
done
phase='database backup target validation'
python3 - "$backup/compose.before.json" "$env_temp/server.json" "$env_temp/sql.json" <<'PY'
import json, sys
services = json.load(open(sys.argv[1], encoding='utf-8'))['services']
def running(path):
    items = json.load(open(path, encoding='utf-8'))
    if not isinstance(items, list) or any(not isinstance(item, str) or '=' not in item for item in items):
        raise SystemExit('Cannot validate running database environment; no values were printed.')
    return dict(item.split('=', 1) for item in items)
def nonblank(value):
    return isinstance(value, str) and bool(value.strip())
def default_port(env, key):
    try:
        return int(env.get(key, 5432)) == 5432
    except (TypeError, ValueError):
        return False
def database(env, server):
    if not isinstance(env, dict):
        raise SystemExit('Database environment must be explicit; no values were printed.')
    user = env.get('POSTGRES_USER')
    name = env.get('POSTGRES_DB') if 'POSTGRES_DB' in env else user
    if not nonblank(name) or not nonblank(user):
        raise SystemExit('Database name/user must be nonblank; no values were printed.')
    if server:
        if env.get('POSTGRES_HOST') != 'sql' or not default_port(env, 'POSTGRES_PORT'):
            raise SystemExit('Only the local sql service on port 5432 is supported; no values were printed.')
    elif not default_port(env, 'PGPORT'):
        raise SystemExit('The SQL service must use port 5432; no values were printed.')
    return name, user
targets = (
    database(services['server'].get('environment'), True),
    database(services['sql'].get('environment'), False),
    database(running(sys.argv[2]), True),
    database(running(sys.argv[3]), False),
)
if any(target != targets[0] for target in targets[1:]):
    raise SystemExit('Compose and running app/SQL database names or roles differ; no values were printed.')
PY
cleanup_env
env_temp=
{ printf 'services:\n'; cat "$backup/rollback.services.yaml"; } >"$backup/rollback.images.yaml"
cat >"$backup/ROLLBACK.txt" <<EOF
Previous Git commit: $old_head
Target Git commit:   $commit
Private database archive: $backup/database.dump
Private configuration:   $backup/files/
Previous Docker images:  $backup/previous-images.txt

The new API runs database migrations at startup. Do not assume old images are
compatible with the migrated database. No database restore is automatic.
Review schema compatibility first, or arrange a maintenance window and an
explicit database/configuration restore from this backup with an administrator.
Restoring the database discards later writes, so it needs a separate decision.

If schema compatibility is confirmed, use the preserved images without rebuilding:
cd '$repo'
docker compose -f docker-compose.yml -f compose.importers.yaml -f '$backup/rollback.images.yaml' up -d --no-deps --no-build --force-recreate server client
Use sudo before docker only if needed. Do not delete SQL/media mounts or volumes.
EOF

phase='GitHub commit validation'
private git fetch --no-tags origin master
[[ "$(git rev-parse refs/remotes/origin/master)" = "$commit" ]] || die 'GitHub master differs from the reviewed SHA; review its new head first.'
git merge-base --is-ancestor "$old_head" "$commit" || die 'The existing branch cannot fast-forward to the reviewed commit.'
if ! git diff --quiet -- docker-compose.yml; then
    git diff --quiet "$old_head" "$commit" -- docker-compose.yml || die 'The update changes the locally customized Compose file; resolve that merge explicitly.'
fi
deploy_branch="codex/production-$stamp"
(
    # Git source files need normal read permissions in non-root build stages.
    # Keep the outer restrictive mask for private backups and configuration.
    umask 022
    private git switch -c "$deploy_branch"
    private git merge --ff-only "$commit"
)
[[ "$(git rev-parse HEAD)" = "$commit" ]] || die 'The checkout does not match the reviewed commit.'
[[ -f compose.importers.yaml ]] || die 'The reviewed commit must contain compose.importers.yaml.'
"${compose[@]}" config --format json >"$backup/compose.after.json" 2>>"$log" || die 'Cannot resolve the deployment Compose configuration.'
python3 - "$backup/compose.before.json" "$backup/compose.after.json" <<'PY'
import json, sys
before, after = [json.load(open(p, encoding='utf-8'))['services'] for p in sys.argv[1:]]
for service in ('server', 'client'):
    for key in ('build', 'volumes', 'ports', 'environment'):
        if before[service].get(key) != after[service].get(key):
            raise SystemExit('The overlay must preserve existing builds, mounts, ports and environment.')
    if after[service].get('restart') != 'unless-stopped':
        raise SystemExit('Application restart policies are missing.')
if before['sql'] != after['sql']:
    raise SystemExit('The deployment must preserve SQL configuration exactly.')
helper = after['fa-import']
if helper.get('ports') or helper.get('network_mode') == 'host' or helper.get('build', {}).get('target') != 'runtime':
    raise SystemExit('FA helper must use its runtime target and have no published ports.')
PY
phase='image builds (existing site remains online)'
printf 'Building the reviewed images; details are in the private deployment log.\n'
private "${compose[@]}" build server client fa-import
private "${compose[@]}" up -d --no-deps --no-build --wait --wait-timeout 180 fa-import

phase='backup capacity check'
database_bytes=$("${docker_cmd[@]}" exec "$sql_container" sh -c 'PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -U "${POSTGRES_USER:?}" -d "${POSTGRES_DB:-$POSTGRES_USER}" -Atqc "SELECT pg_database_size(current_database())"' 2>>"$log")
[[ "$database_bytes" =~ ^[0-9]+$ ]] || die 'Cannot estimate database backup size.'
media_bytes=0
if ((media_backup == 1)); then
    media_kib=$("${docker_cmd[@]}" run --rm --network none --read-only --user 0:0 --mount "type=bind,source=$data,target=/snapshot,readonly" --entrypoint du "$sql_image" -sk /snapshot 2>>"$log")
    media_kib=${media_kib%%[[:space:]]*}
    [[ "$media_kib" =~ ^[0-9]+$ ]] || die 'Cannot estimate media backup size.'
    media_bytes=$((media_kib * 1024))
fi
available=$(df -PB1 -- "$backup" | awk 'NR==2 {print $4}')
[[ "$available" =~ ^[0-9]+$ ]] || die 'Cannot determine backup free space.'
((available > (database_bytes + media_bytes) * 12 / 10 + 2147483648)) || die 'Insufficient free backup space; provide another backup root or an existing separate media backup.'

if [[ "${docker_cmd[0]}" = sudo ]]; then
    sudo -v || die 'Refresh Docker administration privileges before entering maintenance.'
fi
phase='consistent database and media backup'
printf 'Entering maintenance: stopping client/server for the consistent backup.\n'
stopped=1
private "${base[@]}" stop client server
"${docker_cmd[@]}" exec "$sql_container" sh -c 'PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump -U "${POSTGRES_USER:?}" -d "${POSTGRES_DB:-$POSTGRES_USER}" --format=custom' >"$backup/database.dump" 2>>"$log"
[[ -s "$backup/database.dump" ]] || die 'The database dump is empty.'
"${docker_cmd[@]}" exec -i "$sql_container" pg_restore --list <"$backup/database.dump" >"$backup/database.contents.txt" 2>>"$log"
if ((media_backup == 1)); then
    private "${docker_cmd[@]}" run --rm --network none --read-only --user 0:0 --mount "type=bind,source=$data,target=/snapshot,readonly" --mount "type=bind,source=$backup,target=/backup" --entrypoint sh "$sql_image" -c 'umask 077; tar -C /snapshot -cpf /backup/media.tar . && tar -tf /backup/media.tar >/dev/null && chown "$1:$2" /backup/media.tar' sh "$(id -u)" "$(id -g)"
    [[ -s "$backup/media.tar" ]] || die 'The media archive is empty.'
fi

if ((post_edit_policy == 1)); then
    phase='requested post-edit policy'
    python3 - "$repo/server/config.yaml" "$backup/files/server.config.yaml" <<'PY'
import hashlib, os, stat, sys, tempfile
import yaml
path, backup = sys.argv[1:]
original = open(path, 'rb').read()
if hashlib.sha256(original).digest() != hashlib.sha256(open(backup, 'rb').read()).digest():
    raise SystemExit('Private config changed during deployment; review it before updating ranks.')
info = os.stat(path)
try:
    config = yaml.safe_load(original)
except yaml.YAMLError:
    raise SystemExit('Production config is not valid YAML; no configuration values were printed.')
privileges = config.setdefault('privileges', {})
for field in ('content', 'thumbnail', 'safety', 'source', 'relations', 'flags', 'description'):
    privileges['posts:edit:' + field] = 'moderator'
for field in ('tags', 'notes', 'own:description'):
    privileges['posts:edit:' + field] = 'regular'
descriptor, temporary = tempfile.mkstemp(prefix='.config.deploy-', dir=os.path.dirname(path))
try:
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        yaml.safe_dump(config, stream, sort_keys=False, allow_unicode=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(temporary, info.st_uid, info.st_gid)
    os.chmod(temporary, stat.S_IMODE(info.st_mode))
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
fi
phase='new application startup and migrations'
new_started=1
private "${compose[@]}" up -d --no-deps --no-build --force-recreate server client
phase='client and API health verification'
python3 - "$port" >>"$log" 2>&1 <<'PY'
import json, sys, time, urllib.request
base = 'http://127.0.0.1:' + sys.argv[1]
for attempt in range(90):
    try:
        for path in ('/', '/api/info'):
            request = urllib.request.Request(base + path, headers={'Accept': 'application/json' if path == '/api/info' else 'text/html'})
            with urllib.request.urlopen(request, timeout=3) as response:
                if response.status != 200:
                    raise ValueError('Unexpected response status')
                if path == '/api/info':
                    if response.headers.get_content_type() != 'application/json' or not isinstance(json.load(response), dict):
                        raise ValueError('Expected the real API JSON response')
        break
    except Exception:
        if attempt == 89:
            raise SystemExit('Client/API failed the local HTTP check.')
        time.sleep(2)
PY
for service in server client fa-import; do
    container=$("${compose[@]}" ps -q "$service" 2>>"$log")
    [[ -n "$container" && "$("${docker_cmd[@]}" inspect --format '{{.State.Running}} {{.HostConfig.RestartPolicy.Name}}' "$container" 2>>"$log")" = 'true unless-stopped' ]] || die 'A deployed service is not running with its restart policy.'
done
printf '%s\n' "$commit" >"$backup/deployed-commit.txt"
printf '%s\n' "$deploy_branch" >"$backup/deployed-branch.txt"
printf 'Deployment complete: %s\nClient and API respond successfully. Private backup: %s\n' "$commit" "$backup"
