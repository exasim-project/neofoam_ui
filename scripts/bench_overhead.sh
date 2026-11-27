#!/usr/bin/env bash
# Overhead of the stepper on a pitzDaily run: scripts/bench_overhead.sh [bench_overhead.py args]
#
# Env: OPENFOAM_BASHRC (default ~/OpenFOAM/OpenFOAM-v2406/etc/bashrc), NEOFOAM_DIR.
# --catalyst PREFIX for the field modes. See scripts/bench_overhead.py.
args=("$@")
set --
# shellcheck disable=SC1090
source "${OPENFOAM_BASHRC:-$HOME/OpenFOAM/OpenFOAM-v2406/etc/bashrc}"
set -- "${args[@]}"
set -euo pipefail
cd "$(dirname "$0")/.."
exec pixi run python scripts/bench_overhead.py "$@"
