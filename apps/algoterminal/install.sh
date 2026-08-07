#!/bin/sh
# Install AlgoTerminal.
#
#   curl -fsSL https://raw.githubusercontent.com/<org>/<repo>/main/install.sh | sh
#
# Installs pipx if missing, then installs AlgoTerminal as an isolated,
# globally-available `algoterminal` command.

set -e

REPO_URL="${ALGOTERMINAL_REPO_URL:-}"
PACKAGE_SPEC="${ALGOTERMINAL_PACKAGE_SPEC:-algoterminal-cli}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$1"; }
error() { printf '\033[1;31m==>\033[0m %s\n' "$1" >&2; }

command_exists() { command -v "$1" >/dev/null 2>&1; }

if ! command_exists python3; then
  error "python3 is required but was not found on PATH."
  error "Install Python 3.11+ from https://www.python.org/downloads/ and re-run this script."
  exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
info "Found Python ${PY_VERSION}"

if ! command_exists pipx; then
  info "pipx not found — installing it via pip..."
  python3 -m pip install --user pipx
  python3 -m pipx ensurepath
  warn "pipx was just installed. You may need to restart your shell for PATH changes to take effect."
fi

info "Installing AlgoTerminal..."
if [ -n "$REPO_URL" ]; then
  pipx install --force "git+${REPO_URL}"
elif [ -f "./pyproject.toml" ]; then
  pipx install --force .
else
  pipx install --force "$PACKAGE_SPEC"
fi

info "Done. Run 'algoterminal' to launch the research workbench."
