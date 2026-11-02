#!/usr/bin/env sh
# Regenerate the Python stubs of the engine's gRPC APIs.
#
#   proto/algogators/*.proto  (copied from services/trade-ngin; the commit is in proto/algogators/SOURCE;
#                              scripts/sync_protos.sh copies and checks them)
#   -> algogators/<api>_pb2.py, algogators/<api>_pb2_grpc.py
#   -> algogators/versions.py  (API_VERSIONS, parsed from each .proto's version header by
#                              scripts/gen_versions.py, also copied from services/trade-ngin)
#
# Needs grpcio-tools at the version pinned in requirements-dev.txt. CI runs this and fails
# when the committed files differ from what it generates.
set -eu
cd "$(dirname "$0")/.."
rm -rf algogators
python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/algogators/*.proto
printf '%s\n' '"""Generated gRPC stubs of the engine APIs (see scripts/gen_proto.sh). Do not edit."""' \
    > algogators/__init__.py
python scripts/gen_versions.py proto/algogators algogators/versions.py
