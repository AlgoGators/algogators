### Set up

1. Download docker. https://www.docker.com/products/docker-desktop/

2. Open Docker -- Docker must be open to activate the docker engine for building applications

### Building the image

Build from the engine's folder, `services/trade-ngin/` of the AlgoGators repository (the build
context must be that folder: the image copies `services/rpc/` and `proto/` from it):
```bash
docker build -t trade-ngin .
```

The `Dockerfile` has three stages:
- `builder` installs the build dependencies (Eigen3, NLopt, Apache Arrow, libpq, and nlohmann_json
  and libpqxx from source), patches a few missing standard includes into the sources, and builds
  the engine in Release with the tests off;
- `rpc-build` builds the Python environment `/opt/rpc` of the engine's gRPC server and of the book
  e-mail's report module;
- the runtime stage installs only the runtime libraries, cron and `python3`, takes the built
  engine and `/opt/rpc`, sets `TZ=America/New_York`, and installs the cron table
  `live_portfolio.cron`.

The include patches (for example `#include <atomic>` in `include/trade_ngin/order/order_manager.hpp`
and `src/order/order_manager.cpp`) are already lines of the `Dockerfile`; nothing needs adding by
hand.

### Running the image

Started with no command, the container snapshots its environment for cron
(`scripts/docker-entrypoint.sh`) and runs cron in the foreground; the cron job runs
`scripts/run_live_portfolio.sh`. Mount the local `config/` directory (made from
`config_template/`) so the runs have their database and e-mail credentials:
```bash
docker run -d --name trade-ngin -v "$(pwd)/config:/app/config" -v "$(pwd)/logs:/app/logs" trade-ngin
```

Started with the command `rpc`, the same image is `engine-rpc`, the engine's gRPC server and the
QT desk; `deploy/engine-rpc.compose.yml` shows how it is run, and
[SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md), section 4.8, describes both shapes.

For a shell inside the image, with the built runners under `/app/build/bin/Release/`:
```bash
docker run -it --entrypoint /bin/bash trade-ngin
```
