#!/usr/bin/env sh
# Copy the engine's gRPC contract from services/trade-ngin in this repository, or check the copy.
#
#   scripts/sync_protos.sh           copy services/trade-ngin/proto/algogators/*.proto and
#                                    services/trade-ngin/services/rpc/gen_versions.py here, record
#                                    the commit in proto/algogators/SOURCE, then run
#                                    scripts/gen_proto.sh
#   scripts/sync_protos.sh --check   fail unless every .proto here is byte-identical to the
#                                    engine's, the set of .proto files matches, and
#                                    scripts/gen_versions.py is the engine's code (compared as
#                                    parsed Python, so this repository's formatter may reflow it)
#
# Local files only: the engine lives in this repository since #90. CI runs --check
# (svc-research-api.quality.yml) on every change to either side.
set -eu
cd "$(dirname "$0")/.."

ENGINE=../trade-ngin
SOURCE=proto/algogators/SOURCE

if [ "${1:-}" = "--check" ]; then
    fail=0
    local_list=$(cd proto/algogators && ls *.proto | sort)
    engine_list=$(cd "$ENGINE/proto/algogators" && ls *.proto | sort)
    if [ "$local_list" != "$engine_list" ]; then
        echo "proto set differs from services/trade-ngin: here [$local_list] engine [$engine_list]" >&2
        fail=1
    fi
    for name in $engine_list; do
        if ! cmp -s "$ENGINE/proto/algogators/$name" "proto/algogators/$name"; then
            echo "proto/algogators/$name differs from services/trade-ngin/proto/algogators/$name" >&2
            fail=1
        fi
    done
    if ! python - "$ENGINE/services/rpc/gen_versions.py" scripts/gen_versions.py <<'EOF'
import ast, sys
a, b = (ast.dump(ast.parse(open(p, encoding="utf-8").read())) for p in sys.argv[1:3])
sys.exit(a != b)
EOF
    then
        echo "scripts/gen_versions.py differs from services/trade-ngin/services/rpc/gen_versions.py" >&2
        fail=1
    fi
    [ "$fail" = 0 ] && echo "the vendored protos match services/trade-ngin"
    exit "$fail"
fi

[ $# -eq 0 ] || { echo "usage: scripts/sync_protos.sh [--check]" >&2; exit 2; }
rm -f proto/algogators/*.proto
cp "$ENGINE"/proto/algogators/*.proto proto/algogators/
cp "$ENGINE/services/rpc/gen_versions.py" scripts/gen_versions.py
printf 'source: services/trade-ngin (AlgoGators/algogators)\ncommit: %s\n' \
    "$(git rev-parse HEAD)" > "$SOURCE"
sh scripts/gen_proto.sh
echo "copied the engine's protos; review and commit proto/algogators, scripts/gen_versions.py, algogators/"
