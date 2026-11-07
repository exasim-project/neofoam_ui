#!/usr/bin/env bash
# Open one of NeoFOAM's multiRun "limitations" probe cases in the DAG stepper.
#
#   scripts/probe.sh <case name | case dir> [-- extra neofoam-ui args, e.g. --port 8777]
#
# The cases live in $NEOFOAM_DIR/test/solver/multiRun/limitations/cases (pair, chain,
# two_interfaces, no_coordinates, renamed_fields, convergence_typo, forgotten_graph); each
# region runs the test-only solver `probe` (README.md there). The case is copied to a fresh
# temporary directory, so a run leaves the checkout untouched.
#
# Env: NEOFOAM_DIR (a NeoFOAM checkout or worktree with those cases, default
# ~/libsAndApps/NeoFOAM), NEOFOAM_PYTHON (default ~/libsAndApps/NeoFOAM/.venv/bin/python),
# NEOFOAM_SITE (optional: a directory with a sitecustomize.py that points NeoFOAM's editable
# install at $NEOFOAM_SRC; set to run a worktree's src with the main checkout's venv),
# NEOFOAM_SRC (default $NEOFOAM_DIR/src), PROBE_COUPLINGS=hand (couple chain and
# two_interfaces by hand, below the regionCoupling.yaml that cannot describe them),
# OPENFOAM_BASHRC (default ~/OpenFOAM/OpenFOAM-v2406/etc/bashrc; pybFoam needs it).
# OpenFOAM's bashrc is not strict-mode clean and parses "$@" as overrides:
# source it first, with the script arguments hidden.
args=("$@")
set --
# shellcheck disable=SC1090
source "${OPENFOAM_BASHRC:-$HOME/OpenFOAM/OpenFOAM-v2406/etc/bashrc}"
set -- "${args[@]}"
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
neofoam="${NEOFOAM_DIR:-$HOME/libsAndApps/NeoFOAM}"
limits="$neofoam/test/solver/multiRun/limitations"
if [[ $# -lt 1 || "$1" == "--" ]]; then
    echo "usage: scripts/probe.sh <case name | case dir> [-- neofoam-ui args]" >&2
    exit 2
fi
case_arg="$1"; shift
if [[ "${1:-}" == "--" ]]; then shift; fi
if [[ ! -f "$limits/probe_ui.py" ]]; then
    echo "no probe cases in $limits (set NEOFOAM_DIR to a NeoFOAM with them)" >&2
    exit 2
fi
source_dir="$case_arg"
[[ -d "$source_dir" ]] || source_dir="$limits/cases/$case_arg"
if [[ ! -d "$source_dir/system" ]]; then
    echo "no case $case_arg (cases: $(ls "$limits/cases" | tr '\n' ' '))" >&2
    exit 2
fi
run_root="$(mktemp -d "${TMPDIR:-/tmp}/neofoam-probe.XXXXXX")"
case_dir="$run_root/$(basename "$(realpath "$source_dir")")"
cp -r "$source_dir" "$case_dir"

export NEOFOAM_SRC="${NEOFOAM_SRC:-$neofoam/src}"
export PYTHONPATH="$limits${NEOFOAM_SITE:+:$NEOFOAM_SITE}${PYTHONPATH:+:$PYTHONPATH}"
cd "$here"
exec pixi run neofoam-ui --solver probe_ui:probe --case "$case_dir" \
    --python "${NEOFOAM_PYTHON:-$HOME/libsAndApps/NeoFOAM/.venv/bin/python}" "$@"
