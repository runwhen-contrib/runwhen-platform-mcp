#!/usr/bin/env bash
# Local validation harness for the RunSession triage SLX scripts.
#
#   ./run_local.sh <script.py> <RUNSESSION_ID> [KEY=VAL ...]
#
# Examples:
#   ./run_local.sh runsession_db_snapshot.py 200759
#   ./run_local.sh runsession_logs.py 200764 LOOKBACK=3h TAIL=800
#   ./run_local.sh runsession_redis_taskiq.py 200759
#   ./run_local.sh runsession_usearch_index.py 200759
#   ./run_local.sh runsession_timeline.py 200759
#
# It sources .env.local (the local-only creds file: TEST_PG_CORE_*,
# TEST_PG_USEARCH_*, TEST_REDIS_PASSWORD, TEST_NEO4J_PASSWORD, kubeconfig,
# KUBE_CONTEXT), runs the script's main() via importlib WITHOUT adding an
# `if __name__` guard (the committed SLX must not have one), and prints the
# returned issues JSON plus all stdout.
set -euo pipefail
cd "$(dirname "$0")"

SCRIPT="${1:?usage: run_local.sh <script.py> <RUNSESSION_ID> [KEY=VAL ...]}"
RSID="${2:?provide a RunSession id or name}"
shift 2 || true

[ -f .env.local ] || { echo "ERROR: .env.local missing (local creds file)"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q --disable-pip-version-check 'psycopg[binary]' redis neo4j requests 2>/dev/null || true

set -a; . ./.env.local; set +a
export RUNSESSION_ID="$RSID"
for kv in "$@"; do export "$kv"; done

python3 - "$SCRIPT" <<'PY'
import importlib.util, json, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("slx", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)          # runs module top-level (no if __name__)
issues = mod.main()
print("\n================ ISSUES (contract output) ================")
print(json.dumps(issues, indent=2, default=str))
print(f"\n{len(issues)} issue(s).")
PY
