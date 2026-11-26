#!/usr/bin/env bash
# Forced command for the repository's deployment key. stdin is the CI release archive.
set -Eeuo pipefail
umask 077
base="$HOME/qijian"
rev=${SSH_ORIGINAL_COMMAND:-}
[[ $rev =~ ^[a-f0-9]{40}$ ]] || exit 2
cd "$base"
exec 9>deploy.lock
flock -w 600 9
candidate=$(mktemp -d "$base/releases/$rev.XXXXXX")
tar -xzf - --no-same-owner -C "$candidate"
test -s "$candidate/dist-local/index.html"
test -s "$candidate/local/server.py"
digest=$(sha256sum "$candidate/local/requirements.txt" | cut -d' ' -f1)
envdir="$base/envs/$digest"
if [[ ! -f $envdir/.ready ]]; then
    python3 -m venv --without-pip "$envdir"
    python3 -m pip --python "$envdir" install --no-cache-dir -r "$candidate/local/requirements.txt"
    touch "$envdir/.ready"
fi
ln -s "$envdir" "$candidate/venv"
previous=$(readlink -f current || true)
source "$base/config/service.env"
: "${HOTEL_STORE_ORIGIN:?HTTPS store origin required}"
host=${HOTEL_STORE_ORIGIN#https://}
activate() {
    ln -sfn "$1" "$base/next"
    mv -Tf "$base/next" "$base/current"
}
snapshot=$(mktemp -d "$base/backups/$rev.XXXXXX")
systemctl --user stop qijian.service
recover() {
    rc=$?
    trap - ERR
    set +e
    systemctl --user stop qijian.service
    if [[ -f $snapshot/complete ]]; then
        mkdir "$snapshot/failed-data"
        rsync -a --exclude=/models --backup --backup-dir="$snapshot/failed-data" --delete "$snapshot/data/" "$base/data/" || exit 1
    fi
    if [[ -d $previous && $previous != "$base/current" ]]; then
        activate "$previous" && systemctl --user start qijian.service
    fi
    exit "$rc"
}
trap recover ERR
mkdir "$snapshot/data"
rsync -a --exclude=/models "$base/data/" "$snapshot/data/"
python3 - "$snapshot/data" <<'PY'
import pathlib, sqlite3, sys
for path in pathlib.Path(sys.argv[1]).rglob('*.sqlite3'):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('pragma integrity_check').fetchall() != [('ok',)]:
            raise ValueError(f'Backup failed integrity check: {path.name}')
PY
touch "$snapshot/complete"
activate "$candidate"
systemctl --user start qijian.service
ready=false
for attempt in {1..60}; do
    if curl -fsS --max-time 3 -H "Host: $host" http://127.0.0.1:8765/api/ping 2>/dev/null | grep -q '"ok":true'; then
        ready=true
        break
    fi
    sleep 1
done
[[ $ready == true ]]
curl -fsS -H "Host: $host" http://127.0.0.1:8765/ | grep -q 'id="root"'
curl -fsS -H "Host: $host" http://127.0.0.1:8765/api/session | grep -q '"store":true'
[[ $(curl -s -o /dev/null -w '%{http_code}' -H "Host: $host" http://127.0.0.1:8765/api/records) == 401 ]]
printf '%s\n' "$rev" > "$candidate/REVISION"
echo "Activated $rev"
