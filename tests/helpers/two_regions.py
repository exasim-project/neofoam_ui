"""A fake multiRun: two regions, each a solver on its own thread, that exchange data only
through a comm channel with blocking receives (as NeoFOAM's ``run_participants``), reported through
the NeoFOAM observer adapter (``_Stepper``), with no neofoam needed.

Each region's graph is ``time_loop { set_time_step, [prep,] <r>.couple { solve } }``; the
coupling op's test sends the region's ``x`` to its partner and then receives the partner's
(blocking). Region ``b`` has an extra ``prep`` op, so it reaches its coupling op one step
after ``a``: ``a`` then waits for ``b``.
"""

import threading
from types import SimpleNamespace

import numpy as np

from neofoam_ui.adapters.neofoam._fields import _FieldAccess
from neofoam_ui.adapters.neofoam._observed import _Stepper
from neofoam_ui.engine import ThreadedProgram


class Closed(RuntimeError):
    """A region waits for a region that left."""


class CommChannel:
    """A mailbox per (source, target, tag); a receive blocks until its message is there."""

    def __init__(self):
        self.messages, self.closed, self.cv = {}, set(), threading.Condition()

    def send(self, source, target, tag, message):
        with self.cv:
            self.messages.setdefault((source, target, tag), []).append(message)
            self.cv.notify_all()

    def receive(self, source, target, tag):
        with self.cv:
            while not self.messages.get((source, target, tag)):
                if source in self.closed:
                    raise Closed(f"{target} waits for {tag} from {source}, which left")
                self.cv.wait()
            return self.messages[(source, target, tag)].pop(0)

    def close(self, region):
        with self.cv:
            self.closed.add(region)
            self.cv.notify_all()


class CouplingLoopOp:
    """A region's coupling op as the adapter reads it (no ports: no coupling lines)."""

    def __init__(self, region):
        self.region, self.label, self.ports = region, f"{region}.couple", ()
        self.is_leader, self.controller = region == "b", None
        self.func = lambda ctx: None


class Op:
    """Just enough of neofoam's Operation; a loop's ``test(ctx, i)`` decides its passes."""

    def __init__(self, name, children=None, test=None, body=None, func=None):
        self.metadata = SimpleNamespace(op_name=name, name=name, domain_name=None,
                                        operation_number=None, description="")
        self.operation_type = "iterative" if children is not None else "sequential"
        self.sub_operations = children or []
        self.body = body or (lambda ctx: None)
        self.test = test
        self.func = func or SimpleNamespace(func=lambda ctx: None)

    def run(self, obs, ctx):
        if self.operation_type == "sequential":
            obs.before_operation(self, ctx)
            self.body(ctx)
            obs.after_operation(self, ctx)
            return
        i = 1
        while True:
            obs.before_loop_test(self, ctx, i)
            go = self.test(ctx, i)
            obs.after_loop_test(self, ctx, i, go)
            if not go:
                return
            for child in self.sub_operations:
                child.run(obs, ctx)
            i += 1


def _region_graph(region, partner, comm_channel, iterations, fail_at):
    def couple(ctx, i):
        if i > iterations:
            return False
        comm_channel.send(region, partner, f"x{i}", float(ctx.fields["x"][0]))
        ctx.models["received"] = comm_channel.receive(partner, region, f"x{i}")
        return True

    def solve(ctx):
        if fail_at == (region, ctx.models["solves"] + 1):
            raise RuntimeError(f"boom in {region}")
        ctx.models["solves"] += 1
        ctx.fields["x"] = np.array([1.0 + 0.5 * ctx.models["received"]])

    def step(ctx):
        ctx.time.value += 1.0

    body = [Op("set_time_step", body=step)]
    if region == "b":
        body.append(Op("prep"))
    body.append(Op(f"{region}.couple", [Op("solve", body=solve)], test=couple,
                   func=CouplingLoopOp(region)))
    return [Op("time_loop", body, test=lambda ctx, i: i <= 1)]


class TwoRegions(_FieldAccess):
    """A self-driving solver whose run is two region threads (see the module)."""

    name = "multiRun"

    def __init__(self, iterations=2, fail_at=None):
        self.iterations, self.fail_at = iterations, fail_at
        self.comm_channel = CommChannel()
        self.contexts = {}

    def program(self, session):
        thread = self.thread = session.program_thread = ThreadedProgram(self._run)
        self.stepper = _Stepper(session, thread.pause, thread.lanes)
        for event in thread:
            yield event

    def _region(self, region, partner):
        stepper, comm_channel = self.stepper, self.comm_channel
        step = SimpleNamespace(name="fields", region=region, depends_on=[], category="field",
                               initializer=lambda ctx: None)
        stepper.init_steps([step])
        stepper.before_init_step(step, {})
        coupling = SimpleNamespace(group=("a", "b"), comm_channel=comm_channel, parallel=False)
        ctx = SimpleNamespace(region=region, fields={"x": np.zeros(1)}, mesh=None,
                              models={"coupling": SimpleNamespace(coupling=coupling), "solves": 0},
                              time=SimpleNamespace(value=0.0, delta_t=1.0, index=0, write_time=False))
        stepper.after_init_step(step, {}, ctx)
        ops = _region_graph(region, partner, comm_channel, self.iterations, self.fail_at)
        stepper.operations(SimpleNamespace(ops=ops), ctx)
        for op in ops:
            op.run(stepper, ctx)
        return ctx

    def _run(self):
        """As NeoFOAM's ``run_participants``: a thread per region; a failing one closes the
        comm channel for all; the first error is raised once every thread ended."""
        errors = []

        def run(region, partner):
            try:
                self.contexts[region] = self._region(region, partner)
            except BaseException as error:  # noqa: BLE001
                errors.append(error)
                for other in ("a", "b"):
                    self.comm_channel.close(other)
            finally:
                self.comm_channel.close(region)

        threads = [threading.Thread(target=run, args=(r, p), name=r) for r, p in (("a", "b"), ("b", "a"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        return dict(self.contexts)
