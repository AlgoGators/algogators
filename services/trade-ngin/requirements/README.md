Trade Ngin - Requirements

This folder contains ready-to-run scripts to install system dependencies for building and running the project.

Supported OS:
- Ubuntu 22.04+ (Debian-based)
- macOS (Apple Silicon and Intel via Homebrew)

Usage
1) Ubuntu
```bash
sudo bash requirements/install_ubuntu.sh
```

2) macOS (Homebrew required)
```bash
bash requirements/install_macos.sh
```

Installed Dependencies
- Build tools: cmake, make, gcc/clang, pkg-config, git (macOS also jq)
- Testing: GoogleTest (GTest); on Ubuntu built from source and installed
- Libraries:
  - nlohmann_json (on Ubuntu built from source)
  - Apache Arrow (libarrow, dataset)
  - PostgreSQL client libs (libpq)
  - libpqxx (C++ client for Postgres; on Ubuntu built from source)
  - Eigen3 and NLopt, which the build requires (`find_package(Eigen3 REQUIRED)`, `find_package(NLopt REQUIRED)` in `CMakeLists.txt`)
  - libcurl (Ubuntu only)

Notes
- On Ubuntu we add the Apache Arrow APT source and install Arrow dev packages.
- On Ubuntu the script installs `libnlopt-dev`; the engine image (`Dockerfile`) installs `libnlopt-cxx-dev` as well and points CMake at it with `-DNLopt_DIR=/usr/lib/x86_64-linux-gnu/cmake/nlopt`. If CMake does not find NLopt, install `libnlopt-cxx-dev` and pass the same option.
- On macOS we use Homebrew for all packages; ensure `/opt/homebrew` is on your PATH for Apple Silicon.
- If libpqxx via Homebrew is not discovered automatically, the project uses pkg-config (`libpqxx.pc`).
- The lint and coverage tools (clang-format, cpplint, lcov, gcovr) are not installed here. `scripts/setup-dev-environment.sh` installs them, but on Linux it is not a substitute for these scripts: it installs neither Eigen3 nor NLopt and does not add the Apache Arrow APT source.
