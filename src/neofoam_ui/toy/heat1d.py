"""Toy solver: 1D transient heat conduction, shaped like a NeoFOAM solver.

The numerics do not matter — this exists to exercise the stepper UI. The
structure mirrors ``incompressibleFluid.execution_graph``: an initialization
DAG with a fork/join, a time loop with an inner (PIMPLE-like) corrector loop, a
guarded op and an op injected by an optional model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from neofoam_ui.engine import InitStep, Op, topological_order

__all__ = ["Heat1D", "Heat1DContext", "run_plain"]


@dataclass
class Heat1DContext:
    x: np.ndarray
    dx: float
    fields: dict[str, np.ndarray]
    end_time: float
    max_delta_t: float
    n_outer: int
    write_interval: int
    source_strength: float
    time: float = 0.0
    delta_t: float = 0.0
    time_index: int = 0
    outer: int = 0
    residual: float = 0.0
    post: dict[str, float] = field(default_factory=dict)


# -- init steps --------------------------------------------------------------------


def _mesh(results: dict[str, Any]) -> dict[str, Any]:
    """Uniform 1D mesh on [0, 1]."""
    n = 40
    dx = 1.0 / n
    return {"x": (np.arange(n) + 0.5) * dx, "dx": dx}


def _controls(results: dict[str, Any]) -> dict[str, Any]:
    """Read system/controlDict + fvSolution (here: constants)."""
    return {"end_time": 0.05, "delta_t": 0.004, "max_delta_t": 0.01, "n_outer": 2,
            "write_interval": 2}


def _field_t(results: dict[str, Any]) -> np.ndarray:
    """Initial temperature: a hot spot in the middle."""
    x = results["mesh"]["x"]
    return 300.0 + 50.0 * np.exp(-(((x - 0.5) / 0.1) ** 2))


def _field_kappa(results: dict[str, Any]) -> np.ndarray:
    """Conductivity, linear in x."""
    x = results["mesh"]["x"]
    return 1e-2 * (1.0 + 0.5 * x)


def _source_model(results: dict[str, Any]) -> dict[str, Any]:
    """Optional model ``heatSource``: volumetric source on the left quarter."""
    x = results["mesh"]["x"]
    return {"strength": 200.0, "mask": (x < 0.25).astype(float)}


# -- the solver --------------------------------------------------------------------


class Heat1D:
    name = "heat1D"

    def init_steps(self) -> list[InitStep]:
        return [
            InitStep("mesh", _mesh, category="mesh", doc=_mesh.__doc__ or ""),
            InitStep("controls", _controls, category="config", doc=_controls.__doc__ or ""),
            InitStep("fields.T", _field_t, ["mesh"], category="field", doc=_field_t.__doc__ or ""),
            InitStep("fields.kappa", _field_kappa, ["mesh"], category="field",
                     doc=_field_kappa.__doc__ or ""),
            InitStep("models.heatSource", _source_model, ["mesh", "fields.T"], category="model",
                     doc=_source_model.__doc__ or ""),
        ]

    def create_context(self, results: dict[str, Any]) -> Heat1DContext:
        c = results["controls"]
        t = results["fields.T"]
        ctx = Heat1DContext(
            x=results["mesh"]["x"],
            dx=results["mesh"]["dx"],
            fields={"T": t.copy(), "T_old": t.copy(), "kappa": results["fields.kappa"].copy(),
                    "flux": np.zeros(len(t) + 1)},
            end_time=c["end_time"],
            max_delta_t=c["max_delta_t"],
            n_outer=c["n_outer"],
            write_interval=c["write_interval"],
            source_strength=results["models.heatSource"]["strength"],
            delta_t=c["delta_t"],
        )
        ctx.fields["source_mask"] = results["models.heatSource"]["mask"]
        return ctx

    def execution_graph(self, ctx: Heat1DContext) -> list[Op]:
        return [
            Op("time_loop", "loop", _time_loop, domain="solutionLoop", number="1",
               test="time < endTime", doc=_time_loop.__doc__ or "", children=[
                Op("set_time_step", "action", _set_time_step, domain="solutionLoop", number="1.1",
                   reads=["deltaT"], writes=["deltaT"], doc=_set_time_step.__doc__ or ""),
                Op("increment_time", "action", _increment_time, domain="solutionLoop",
                   number="1.2", reads=["time"], writes=["time"], doc=_increment_time.__doc__ or ""),
                Op("store_old_time", "action", _store_old_time, domain="heat", number="1.3",
                   reads=["T"], writes=["T_old"], doc=_store_old_time.__doc__ or ""),
                Op("inner_loop", "loop", _inner_loop, domain="outer", number="1.4",
                   test="outer < nOuterCorrectors", doc=_inner_loop.__doc__ or "", children=[
                    Op("assemble", "action", _assemble, domain="heat", number="1.4.1",
                       reads=["T", "kappa"], writes=["flux"], doc=_assemble.__doc__ or ""),
                    Op("solve", "action", _solve, domain="heat", number="1.4.2",
                       reads=["T", "T_old", "flux", "kappa"], writes=["T"], doc=_solve.__doc__ or ""),
                    Op("apply_source", "action", _apply_source, domain="heatSource",
                       number="1.4.3", injected_by="heatSource", reads=["T", "source_mask"],
                       writes=["T"], doc=_apply_source.__doc__ or ""),
                    Op("update_kappa", "action", _update_kappa, domain="material", number="1.4.4",
                       guard="finalIter()", reads=["T"], writes=["kappa"],
                       doc=_update_kappa.__doc__ or ""),
                ]),
                Op("write_output", "action", _write_output, domain="fieldWriter", number="1.5",
                   reads=["T", "kappa"], doc=_write_output.__doc__ or ""),
                Op("post_process", "action", _post_process, domain="postProcess", number="1.6",
                   reads=["T"], doc=_post_process.__doc__ or ""),
            ]),
        ]

    def scalars(self, ctx: Heat1DContext) -> dict[str, Any]:
        return {"time": round(ctx.time, 10), "deltaT": round(ctx.delta_t, 10),
                "timeIndex": ctx.time_index, "outer": ctx.outer,
                "residual": float(f"{ctx.residual:.3e}"), **ctx.post}

    def fields(self, ctx: Heat1DContext) -> dict[str, np.ndarray]:
        return ctx.fields

    def mesh(self, ctx: Heat1DContext) -> dict[str, Any]:
        """Field-view geometry: the cell centres of the 1D mesh."""
        return {"kind": "line", "x": ctx.x, "axes": ["x"]}


# -- operations ----------------------------------------------------------------------


def _time_loop(ctx: Heat1DContext) -> bool:
    """Continue while time < endTime (SolutionLoopPredicate)."""
    return ctx.time < ctx.end_time - 1e-12


def _set_time_step(ctx: Heat1DContext) -> None:
    """Grow deltaT by 20 % up to maxDeltaT, and land exactly on endTime."""
    ctx.delta_t = min(ctx.delta_t * 1.2, ctx.max_delta_t, ctx.end_time - ctx.time)


def _increment_time(ctx: Heat1DContext) -> None:
    """time += deltaT; timeIndex++."""
    ctx.time += ctx.delta_t
    ctx.time_index += 1


def _store_old_time(ctx: Heat1DContext) -> None:
    """Keep T of the previous time step for the time derivative."""
    ctx.fields["T_old"] = ctx.fields["T"].copy()


def _inner_loop(ctx: Heat1DContext) -> bool:
    """Outer corrector loop (pimple.loop()): nOuterCorrectors passes per time step."""
    ctx.outer += 1
    if ctx.outer > ctx.n_outer:
        ctx.outer = 0
        return False
    return True


def _assemble(ctx: Heat1DContext) -> None:
    """Face fluxes -kappa_f dT/dx with zero-gradient walls."""
    t, k = ctx.fields["T"], ctx.fields["kappa"]
    kf = 0.5 * (k[1:] + k[:-1])
    flux = np.zeros(len(t) + 1)
    flux[1:-1] = -kf * (t[1:] - t[:-1]) / ctx.dx
    ctx.fields["flux"] = flux


def _solve(ctx: Heat1DContext) -> None:
    """Explicit Euler update from the assembled fluxes (good enough for a toy)."""
    t_old, flux = ctx.fields["T_old"], ctx.fields["flux"]
    t_new = t_old - ctx.delta_t * (flux[1:] - flux[:-1]) / ctx.dx
    ctx.residual = float(np.abs(t_new - ctx.fields["T"]).max())
    ctx.fields["T"] = t_new


def _apply_source(ctx: Heat1DContext) -> None:
    """heatSource model: T += deltaT * q * mask (injected into the corrector loop)."""
    ctx.fields["T"] = ctx.fields["T"] + ctx.delta_t * ctx.source_strength * ctx.fields["source_mask"]


def _update_kappa(ctx: Heat1DContext) -> None:
    """Temperature-dependent conductivity, only on the final outer corrector."""
    if ctx.outer != ctx.n_outer:
        return
    ctx.fields["kappa"] = 1e-2 * (1.0 + 0.5 * ctx.x) * (1.0 + 1e-3 * (ctx.fields["T"] - 300.0))


def _write_output(ctx: Heat1DContext) -> None:
    """Write fields every writeInterval time steps (prints instead of writing)."""
    if ctx.time_index % ctx.write_interval == 0:
        print(f"Writing fields at time = {ctx.time:.4g}")


def _post_process(ctx: Heat1DContext) -> None:
    """Volume-average and maximum of T."""
    ctx.post = {"T_mean": round(float(ctx.fields["T"].mean()), 6),
                "T_max": round(float(ctx.fields["T"].max()), 6)}


def run_plain(solver: Heat1D | None = None) -> Heat1DContext:
    """Run without the stepper — the reference for equivalence tests."""
    solver = solver or Heat1D()
    results: dict[str, Any] = {}
    for step in topological_order(solver.init_steps()):
        results[step.name] = step.func(results)
    ctx = solver.create_context(results)

    def run(ops: list[Op]) -> None:
        for op in ops:
            if op.kind == "action":
                op.func(ctx)
            else:
                while op.func(ctx):
                    run(op.children)

    run(solver.execution_graph(ctx))
    return ctx
