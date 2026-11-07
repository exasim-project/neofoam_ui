"""Heat1D run by its own loop in a thread that reports to the session (as NeoFOAM does
through its execution observer); ``RankSelfDriven`` offsets T by 100 per MPI rank."""

import os
import time

from neofoam_ui.engine import Event, ThreadedProgram, assign_ids, topological_order
from neofoam_ui.parallel import rank_info
from neofoam_ui.toy.heat1d import Heat1D


class SelfDriven(Heat1D):
    """Heat1D, but run by its own loop in a thread that reports to the session."""

    def __init__(self, fail_at=None):
        super().__init__()
        self.fail_at = fail_at

    def program(self, session):
        thread = self.thread = session.program_thread = ThreadedProgram(
            lambda: self._run(session, thread.pause))
        yield from thread

    def _run(self, s, pause):
        s.init = topological_order(Heat1D.init_steps(self))
        for step in s.init:
            pause(Event("init", step.id))
            s.results[step.name] = step.func(s.results)
            s.record("init", step.id, step.name, 0.0)
        s.set_context(self.create_context(s.results))
        ops = self.execution_graph(s.ctx)
        assign_ids(ops)
        s.set_ops(ops)
        self._walk(s, pause, s.ops, ())

    def _walk(self, s, pause, ops, stack):
        for op in ops:
            if op.kind == "action":
                pause(Event("exec", op.id, len(stack), stack=stack))
                if op.id == self.fail_at:
                    raise RuntimeError("boom")
                op.func(s.ctx)
                time.sleep(float(os.environ.get("SELF_DRIVEN_SLEEP") or 0))  # a slower solver
                s.record("exec", op.id, op.name, 0.0, stack=stack)
                continue
            i = 1
            while True:
                pause(Event("test", op.id, len(stack), iter=i, stack=stack))
                result = bool(op.func(s.ctx))
                s.record("test", op.id, op.name, 0.0, stack=stack, iter=i, result=result)
                if not result:
                    break
                self._walk(s, pause, op.children, stack + ((op.id, i),))
                i += 1


class RankSelfDriven(SelfDriven):
    def create_context(self, results):
        ctx = super().create_context(results)
        ctx.fields["T"] += 100.0 * rank_info()[0]
        return ctx
