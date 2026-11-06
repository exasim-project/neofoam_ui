#!/usr/bin/env bash
# Prepare a short pitzDaily case and open it in the DAG stepper (NeoFOAM incompressibleFluid).
#
#   scripts/pitzdaily.sh [case-dir] [-- extra neofoam-ui args, e.g. --host 0.0.0.0]
#
# Env: NEOFOAM_DIR (default ~/libsAndApps/NeoFOAM), OPENFOAM_BASHRC
# (default ~/OpenFOAM/OpenFOAM-v2406/etc/bashrc), END_TIME (default 0.002).
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
case_arg="$here/run/pitzDaily"
if [[ $# -gt 0 && "$1" != "--" ]]; then case_arg="$1"; shift; fi
if [[ "${1:-}" == "--" ]]; then shift; fi
case_dir="$(realpath -m -- "$case_arg")"

if [[ ! -d "$case_dir/constant/polyMesh" ]]; then
    mkdir -p "$(dirname "$case_dir")"
    cp -r "$neofoam/tutorials/pitzDaily" "$case_dir"
    rm -rf "$case_dir/0" "$case_dir/__pycache__"
    cp -r "$case_dir/0.orig" "$case_dir/0"
    sed -i "s/^endTime .*/endTime         ${END_TIME:-0.002};/" "$case_dir/system/controlDict"
    (cd "$case_dir" && blockMesh > log.blockMesh)
fi

cd "$here"
exec pixi run neofoam-ui --solver neofoam_ui.adapters.neofoam:incompressible_fluid \
    --case "$case_dir" --python "$neofoam/.venv/bin/python" "$@"
