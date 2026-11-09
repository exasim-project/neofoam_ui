"""Contracts the refactor must keep: entry points, the worker's import boundary, the
trame state and template the browser binds to, and the message trace of a session.

The golden files in ``tests/golden`` were recorded before the refactor; regenerate them
(``NEOFOAM_UI_REGOLD=1 pytest tests/test_contracts.py``) only for an intended change.
"""

import importlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from trame.app import get_server

import neofoam_ui
from neofoam_ui.app import StepperApp
from neofoam_ui.catalyst._config import _PIPELINE
from neofoam_ui.engine import Session
from neofoam_ui.toy.heat1d import Heat1D

GOLDEN = Path(__file__).parent / "golden"
SRC = Path(__file__).resolve().parents[1] / "src"
REGOLD = bool(os.environ.get("NEOFOAM_UI_REGOLD"))


def _golden(name: str, value: str) -> str:
    path = GOLDEN / name
    if REGOLD or not path.exists():
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(value)
    return path.read_text()


# -- entry points ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", ["neofoam_ui.app:main", "neofoam_ui.worker:main",
                                  "neofoam_ui.toy.heat1d:Heat1D",
                                  "neofoam_ui.adapters.neofoam:incompressible_fluid"])
def test_public_entry_points_resolve(spec):
    module, _, attr = spec.partition(":")
    assert callable(getattr(importlib.import_module(module), attr))


def test_the_catalyst_script_stays_at_its_path():
    root = Path(neofoam_ui.__file__).parent
    assert (root / "pipelines" / "view.py").is_file()

    assert Path(_PIPELINE).resolve() == (root / "pipelines" / "view.py").resolve()


def test_the_catalyst_script_is_packaged():
    """``pipelines/`` has no ``__init__``: it ships as a namespace package (setuptools' default)."""
    config = tomllib.loads((SRC.parent / "pyproject.toml").read_text())
    assert config["tool"]["setuptools"]["packages"]["find"].get("namespaces", True)
    assert not (SRC / "neofoam_ui" / "pipelines" / "__init__.py").exists()


def test_the_worker_side_imports_without_trame():
    """The worker runs in the solver's interpreter, which need not have trame."""
    code = (
        "import sys\n"
        "for name in ('trame', 'trame_flow', 'trame_plotly', 'trame_vuetify', 'trame_client',"
        " 'trame_server'):\n"
        "    sys.modules[name] = None\n"
        "import neofoam_ui, neofoam_ui.worker, neofoam_ui.engine, neofoam_ui.parallel\n"
        "import neofoam_ui.catalyst, neofoam_ui.pptables, neofoam_ui.geometry\n"
        "import neofoam_ui.adapters.neofoam, neofoam_ui.toy.heat1d\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_python_m_worker_prints_its_usage():
    proc = subprocess.run([sys.executable, "-m", "neofoam_ui.worker"], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(SRC)})
    assert proc.returncode == 2 and "Protocol" in proc.stderr


# -- what the browser binds to ---------------------------------------------------------------


def _app(name):
    server = get_server(name, client_type="vue3")
    return server, StepperApp(server, "neofoam_ui.toy.heat1d:Heat1D")


def test_trame_state_and_triggers_unchanged():
    server, _ = _app("contract_state")
    state = {k: v for k, v in server.state._pending_update.items() if not k.startswith("trame__")}
    triggers = sorted(n for n in server.controller._triggers if not re.fullmatch(r"trigger__\d+", n))
    text = json.dumps({"state": state, "triggers": triggers}, indent=1, sort_keys=True, default=str) + "\n"
    assert text == _golden("ui_state.json", text)


def test_trame_template_unchanged():
    """The page and the panels' templates (the dock, the sidebars and the phone show those)."""
    server, _ = _app("contract_template")
    pending = server.state._pending_update
    names = ["main"] + sorted(k[len("trame__template_"):] for k in pending
                              if k.startswith("trame__template_nf_p_"))
    html = "".join(f"<!-- template {n} -->\n{pending['trame__template_' + n]}\n" for n in names)
    html = re.sub(r"trigger__\d+", "trigger__N", html)
    html = re.sub(r"(inline_style_|inline_script_|_node_editor_)\d+", r"\1N", html)
    assert html == _golden("ui_template.html", html)


# -- what a session says ----------------------------------------------------------------------


def _trace():
    out = []

    def emit(m):
        kind = m["type"]
        if kind in ("running", "stdout"):
            return
        if kind == "log":
            out.append([kind, m["kind"], m["node"], m.get("iter"), m.get("result"), m["stack"]])
        elif kind == "paused":
            fields = [[r["field"], r["written_by"], r["changed"]] for r in m["summary"]["fields"]]
            out.append([kind, m["event"], m["finished"], m["calls"], sorted(m["writes_seen"].items()),
                        fields])
        elif kind == "graph":
            out.append([kind, m["activity"], m.get("steps") or m.get("ops")])
        else:
            out.append([kind, {k: v for k, v in m.items() if k not in ("traceback",)}])

    session = Session(Heat1D(), emit)
    session.start()
    for mode in ("into", "into", "skip_init", "into", "over", "over", "into", "out", "time_step"):
        session.advance(mode)
    session.breakpoints = {"time_loop/inner_loop/solve"}
    session.advance("run")
    session.breakpoints = set()
    session.advance("run_to", "time_loop/write_output")
    session.advance("run")
    return out


def test_session_message_trace_unchanged():
    text = json.dumps(_trace(), indent=0, sort_keys=True, default=str) + "\n"
    text = re.sub(r'"source": "[^"]*"', '"source": "…"', text)
    assert text == _golden("session_trace.json", text)


# -- the public API ------------------------------------------------------------------------------

PUBLIC = {
    "neofoam_ui": ["Event", "FieldSource", "GraphSolver", "InitStep", "Op", "SelfDrivingSolver", "Session",
                   "Solver", "ThreadedProgram", "assign_ids", "topological_order"],
    "neofoam_ui.engine": ["DEFAULT_REGION", "Event", "Exchange", "FieldSource", "GraphSolver", "InitStep",
                          "MeshProvider", "Mode", "Op", "PassControl", "Peers", "PointMapping", "ProgramContext", "Region", "RegionProvider", "RenderBackend",
                          "Renderer", "ResultObserver", "SelfDrivingSolver", "Session", "Solver",
                          "ThreadedProgram", "UNTRACKED", "ViewFieldProvider", "assign_ids", "topological_order"],
    "neofoam_ui.protocol": ["CATALYST_ENV", "CATALYST_LIVE_ENV", "CATALYST_OUT_ENV", "CATALYST_SCRIPT_ENV",
                            "COMMANDS", "DELAY_ENV", "LOSSY", "MESSAGES", "MODE_ENV", "RANK_OPS", "SYNC_ENV",
                            "UI_URGENT", "WAIT_ENV", "WORKER_URGENT"],
    "neofoam_ui.app": ["StepperApp", "main", "parse_args"],
    "neofoam_ui.extensions": ["ENTRY_POINT_GROUP", "BasePane", "ExtensionRegistry", "Pane", "PaneContext",
                              "PaneToggle"],
    "neofoam_ui.worker": ["load_solver", "main"],
    "neofoam_ui.parallel": ["SYNC_ENV", "rank_info"],
    "neofoam_ui.catalyst": ["ENV", "LIVE_ENV", "OUT_ENV", "SCRIPT_ENV", "InSitu", "configured", "custom_script"],
    "neofoam_ui.geometry": ["cell_centres_geometry", "cell_count", "polyhedral", "read_polymesh"],
    "neofoam_ui.pptables": ["NodeDescriber", "describe_table", "observe", "register_describer", "rows_message"],
    "neofoam_ui.adapters": [],
    "neofoam_ui.adapters.neofoam": ["NeoFoamSolver", "ObservedNeoFoamSolver", "has_observer",
                                    "incompressible_fluid", "neofoam_solver"],
    "neofoam_ui.toy": [],
    "neofoam_ui.toy.heat1d": ["Heat1D", "Heat1DContext", "run_plain"],
}


@pytest.mark.parametrize("module", sorted(PUBLIC))
def test_public_api(module):
    mod = importlib.import_module(module)
    assert sorted(mod.__all__) == sorted(PUBLIC[module])
    assert all(hasattr(mod, name) for name in mod.__all__)


def test_every_other_module_is_private():
    """Every module outside ``PUBLIC`` has a ``_`` in its path and exports nothing itself."""
    root = SRC / "neofoam_ui"
    for path in sorted(root.rglob("*.py")):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        if name in PUBLIC or name == "neofoam_ui.pipelines.view":  # the Catalyst script, loaded by path
            continue
        assert any(p.startswith("_") for p in parts), f"{name}: a public module not in the API"
        assert "__all__ = ()" in path.read_text(), f"{name}: a private module exports names"
