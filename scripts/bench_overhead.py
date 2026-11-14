"""Wall time of a pitzDaily run with and without the stepper around it.

    scripts/bench_overhead.sh [--np 2] [--end-time 0.02] [--repeat 2] [--src DIR] [--modes ...]

Each mode is timed from launch to the end of the last time step (``total``) and from the
solver's start to that end (``run``: ``run()`` called / the first pause left — what the UI
costs the solver itself, without process start-up); the best of ``--repeat`` runs. Modes:

* ``plain``  — NeoFOAM's ``run()`` without the UI;
* ``dag-off`` — the UI worker in Run with every pane closed, the DAG pane too;
* ``ui``     — the UI worker in Run, the DAG pane open (a browser watching), the others closed, delay 0;
* ``field``  — as ``ui`` with the Field pane open and live (in-situ frames, needs Catalyst);
* ``plots``  — as ``ui`` with the Plots pane open;
* ``*-wait`` — the same with "wait for UI" on (``ui-wait``, ``field-wait``, ``plots-wait``).

The UI modes run the real :class:`~neofoam_ui.app.StepperApp` in this process (the
trame server is not started, so no browser renders; everything up to the state push
is real), imported from ``--src`` (default this checkout's ``src``; point it at an
older ``src`` tree to compare). Needs a sourced OpenFOAM and the NeoFOAM interpreter
(``--python``, default ``$NEOFOAM_DIR/.venv/bin/python``); ``--catalyst PREFIX`` for
``field``. Prints a table and appends the results as JSON lines to ``--out``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
from trame.app import get_server

HERE = Path(__file__).resolve().parents[1]
NEOFOAM = Path(os.environ.get("NEOFOAM_DIR", Path.home() / "libsAndApps" / "NeoFOAM"))
SPEC = "neofoam_ui.adapters.neofoam:incompressible_fluid"

PLAIN = r"""
import json, sys, time
from neofoam.solver.incompressibleFluid.incompressibleFluid import run
print("BENCH-START " + json.dumps(time.time()), flush=True)
try:  # the tables' kernels need NeoN/Kokkos, which this solver never initializes (as the UI does)
    from neofoam.solver.neon_runtime import ensure_neon_initialized
    ensure_neon_initialized(["neofoam"])
except ImportError:
    pass
run(["neofoam", "-parallel"] if int(sys.argv[1]) > 1 else ["neofoam"])
print("BENCH-DONE " + json.dumps(time.time()), flush=True)
"""


def prepare(work: Path, end_time: float, np_: int) -> Path:
    """A fresh pitzDaily case (mesh, tables of examples/pitzDaily, decomposed for ``np_``)."""
    case = work / f"pitzDaily-np{np_}"
    if case.exists():
        shutil.rmtree(case)
    shutil.copytree(NEOFOAM / "tutorials" / "pitzDaily", case,
                    ignore=shutil.ignore_patterns("__pycache__", "0"))
    shutil.copytree(case / "0.orig", case / "0")
    control = case / "system" / "controlDict"
    control.write_text("\n".join(f"endTime         {end_time};" if line.startswith("endTime") else line
                                 for line in control.read_text().splitlines()) + "\n")
    for table in (HERE / "examples" / "pitzDaily" / "system").glob("postProcess.*"):
        shutil.copy(table, case / "system")
    subprocess.run(["blockMesh"], cwd=case, check=True, capture_output=True)
    if np_ > 1:
        for entry, value in (("numberOfSubdomains", str(np_)), ("method", "scotch")):
            subprocess.run(["foamDictionary", "-entry", entry, "-set", value, "system/decomposeParDict"],
                           cwd=case, check=True, capture_output=True)
        subprocess.run(["decomposePar", "-force"], cwd=case, check=True, capture_output=True)
    return case


def clean(case: Path) -> None:
    """Remove written time directories and post-processing output of an earlier run."""
    for root in [case, *case.glob("processor*")]:
        for d in root.iterdir():
            if d.is_dir() and d.name not in ("0", "constant", "system") and _number(d.name):
                shutil.rmtree(d)
    shutil.rmtree(case / "postProcessing", ignore_errors=True)


def _number(name: str) -> bool:
    try:
        float(name)
    except ValueError:
        return False
    return True


def run_plain(case: Path, python: str, np_: int) -> dict:
    cmd = [python, "-c", PLAIN, str(np_)]
    if np_ > 1:
        cmd = ["mpirun", "-np", str(np_), *cmd]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=case, capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f"plain run failed:\n{proc.stderr[-3000:]}")
    out = proc.stdout
    def stamps(tag: str) -> list[float]:
        return [json.loads(line.split(" ", 1)[1]) for line in out.splitlines() if line.startswith(tag)]

    done = min(stamps("BENCH-DONE "))
    return {"total": done - t0, "run": done - max(stamps("BENCH-START "))}


async def _run_ui(case: Path, python: str, np_: int, mode: str, catalyst: str | None) -> dict:
    from neofoam_ui.app import StepperApp  # noqa: PLC0415 - from --src, put on sys.path in main()

    server = get_server(f"bench-{mode}-{time.monotonic_ns()}", client_type="vue3")
    app = StepperApp(server, SPEC, case=str(case), python=python, np=np_,
                     catalyst=catalyst if mode.startswith("field") else None)
    s = app.state
    s.delay_ms = 0
    s.fv_on = mode.startswith("field")
    s.fv_field = "U"
    s.fv_live = True
    s.pt_on = mode.startswith("plots")
    if hasattr(app, "viewer"):  # a browser tab watching (an older UI does not ask)
        s.dag_on = mode != "dag-off"
        app.viewer(True)
    elif mode == "dag-off":
        raise SystemExit("this neofoam_ui has no DAG pane toggle")
    if hasattr(app, "wait_ui"):
        s.wait_ui = mode.endswith("-wait")
    elif mode.endswith("-wait"):
        raise SystemExit("this neofoam_ui has no 'wait for UI' option")
    frames = 0
    receive = app.link._on_message  # count the frames the UI got

    def count(msg: dict) -> None:
        nonlocal frames
        frames += msg["type"] == "frame"
        receive(msg)

    app.link._on_message = count
    t0 = time.time()
    await app.spawn()
    while not str(s.status).startswith("paused"):
        await asyncio.sleep(0.01)
    t_init = time.time()
    app.step("run")
    while not (s.finished or s.failed or not s.connected):
        await asyncio.sleep(0.01)
    t_end = time.time()
    app.stop()
    try:
        await asyncio.wait_for(app.proc.wait(), 60)
    except asyncio.TimeoutError:
        app.proc.kill()
    if s.failed or not s.finished:
        raise RuntimeError(f"{mode}: the run did not finish: {s.status}")
    return {"total": t_end - t0, "run": t_end - t_init, "frames": frames}


@click.command(help=__doc__.splitlines()[0])
@click.option("--np", type=int, default=1)
@click.option("--end-time", type=float, default=0.02)
@click.option("--repeat", type=int, default=2)
@click.option("--modes", default="plain,dag-off,ui,field,plots,ui-wait,field-wait,plots-wait")
@click.option("--src", default=str(HERE / "src"), help="neofoam_ui sources to benchmark")
@click.option("--label", default="")
@click.option("--python", default=str(NEOFOAM / ".venv" / "bin" / "python"))
@click.option("--work", help="directory for the case (default: a temporary one)")
@click.option("--out", help="append the results here as JSON lines")
@click.option("--catalyst", metavar="PREFIX", help="Catalyst 2 + ParaView install (the field modes)")
def main(**options: Any) -> None:
    args = SimpleNamespace(**options)
    sys.path.insert(0, os.path.abspath(args.src))
    catalyst = args.catalyst
    work = Path(args.work or tempfile.mkdtemp(prefix="neofoam_ui_bench_"))
    work.mkdir(parents=True, exist_ok=True)
    case = prepare(work, args.end_time, args.np)
    results = []
    for mode in args.modes.split(","):
        if mode.startswith("field") and not catalyst:
            print(f"{mode}: skipped (pass --catalyst)")
            continue
        best: dict | None = None
        for _ in range(args.repeat):
            clean(case)
            if mode == "plain":
                r = run_plain(case, args.python, args.np)
            else:
                r = asyncio.run(_run_ui(case, args.python, args.np, mode, catalyst))
            if best is None or r["total"] < best["total"]:
                best = r
        assert best is not None
        row = {"label": args.label, "np": args.np, "end_time": args.end_time, "mode": mode,
               **{k: round(v, 2) if isinstance(v, float) else v for k, v in best.items()}}
        results.append(row)
        print(json.dumps(row), flush=True)
        if args.out:
            with open(args.out, "a") as f:
                f.write(json.dumps(row) + "\n")
    plain = next((r for r in results if r["mode"] == "plain"), None)
    print(f"\n{'mode':<12}{'total s':>9}{'vs plain':>10}{'run s':>8}{'vs plain':>10}")
    for r in results:
        rel = [f"{100 * (r[k] / plain[k] - 1):+.1f} %" if plain else "" for k in ("total", "run")]
        print(f"{r['mode']:<12}{r['total']:>9.2f}{rel[0]:>10}{r['run']:>8.2f}{rel[1]:>10}")


if __name__ == "__main__":
    main()
