#!/usr/bin/env bash
# Vault logins for the futures release, run inside Linux or WSL. No password or token is ever printed.
#   vault-session.sh login    browser (OIDC) login to Vault; the token lasts 8 hours
#   vault-session.sh creds    new leadership DB login into config/defaults.json, stretched to its 4-hour limit, then the rights check
#   vault-session.sh check    rights check of the login in config/defaults.json, and the time left on it
#   vault-session.sh lease    measures how long a new login can live and shows the role's settings
# The server comes from DB_HOST and DB_PORT in the environment, or from the .env file named by DB_ENV_FILE.
# A login lives at most 4 hours from creation; renewing cannot pass that. The release scripts read the login once when
# they start, so take a fresh one (creds) before every long step: the start, step 13, and step 15.
set -euo pipefail
export VAULT_ADDR="${VAULT_ADDR:-https://vault.algogators.com}" PATH="$HOME/bin:$PATH"
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$(cd "$HERE/../../.." && pwd)"
CFG="$ENGINE/config/defaults.json"
LEASE_FILE="$HOME/.release-lease"

need_vault() {
  command -v vault > /dev/null && return
  mkdir -p "$HOME/bin"
  curl -fsSL -o /tmp/vault.zip https://releases.hashicorp.com/vault/2.0.4/vault_2.0.4_linux_amd64.zip
  python3 -m zipfile -e /tmp/vault.zip "$HOME/bin/" && chmod +x "$HOME/bin/vault" && rm /tmp/vault.zip
}
from_env_file() { [ -n "${DB_ENV_FILE:-}" ] && grep -E "^$1=" "$DB_ENV_FILE" | cut -d= -f2- | tr -d '\r"' || true; }
pg_env() {
  export CFG
  eval "$(python3 -c '
import json, os, shlex
d = json.load(open(os.environ["CFG"]))["database"]
for k, v in (("PGHOST", d["host"]), ("PGPORT", d["port"]), ("PGUSER", d["username"]), ("PGPASSWORD", d["password"]), ("PGDATABASE", d["name"])):
    print("export %s=%s" % (k, shlex.quote(str(v))))')"
}
lease_left() { vault lease renew -format=json -increment=36000 "$(cat "$LEASE_FILE")" | python3 -c 'import json,sys;print(json.load(sys.stdin)["lease_duration"])'; }

case "${1:-}" in
  login)
    need_vault
    vault login -method=oidc -no-print
    echo "LOGGED IN. Token time left: $(vault token lookup -format=json | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["ttl"])') seconds"
    ;;
  creds)
    need_vault
    mkdir -p "$(dirname "$CFG")"
    [ -s "$CFG" ] || cp "$ENGINE/config_template/defaults.json" "$CFG"
    DB_HOST="${DB_HOST:-$(from_env_file DB_HOST)}"; DB_PORT="${DB_PORT:-$(from_env_file DB_PORT)}"
    [ -n "$DB_HOST" ] || { echo "set DB_HOST, or DB_ENV_FILE=<a .env with DB_HOST>"; exit 1; }
    export CFG LEASE_FILE DB_HOST DB_PORT
    vault read -format=json algo-db/creds/leadership | python3 -c '
import json, os, sys
s = json.load(sys.stdin); cfg = os.environ["CFG"]
d = json.load(open(cfg))
d["database"].update(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT") or "5432", name="new_algo_data",
                     username=s["data"]["username"], password=s["data"]["password"])
fd = os.open(cfg, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, json.dumps(d, indent=2).encode()); os.close(fd)
open(os.environ["LEASE_FILE"], "w").write(s["lease_id"])
print("NEW LOGIN written to config/defaults.json")
'
    echo "login good for (seconds): $(lease_left)"
    pg_env
    PGOPTIONS='-c default_transaction_read_only=on' PGCONNECT_TIMEOUT=20 psql -X -f "$HERE/check-login.sql"
    ;;
  check)
    need_vault; export PAGER=cat
    pg_env
    O=$(PGOPTIONS='-c default_transaction_read_only=on' PGCONNECT_TIMEOUT=20 psql -X -f "$HERE/check-login.sql")
    echo "ok lines: $(echo "$O" | grep -c '^ok ')"
    echo "problem lines: $(echo "$O" | grep -cE '^(MISSING|NO SUCH)')"
    echo "$O" | grep -E '^(MISSING|NO SUCH)' || true
    echo "server: $(psql -X -At -c 'show server_version')"
    echo "login seconds left: $(lease_left)"
    echo "vault token seconds left: $(vault token lookup -format=json | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["ttl"])')"
    ;;
  lease)
    need_vault
    bash "$0" creds > /dev/null
    echo "longest life of a new login (seconds): $(lease_left)"
    echo "my rights on the role: $(vault token capabilities algo-db/roles/leadership)"
    vault read -format=json algo-db/roles/leadership 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]
for k in ("default_ttl", "max_ttl", "revocation_statements"):
    print(k + ":", d.get(k))' || echo "role settings: not readable with this token"
    ;;
  *) echo "usage: $0 login|creds|check|lease"; exit 2 ;;
esac
