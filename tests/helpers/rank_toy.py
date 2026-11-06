"""Heat1D whose temperature is offset by 100 per MPI rank (as seen by neofoam_ui.parallel)."""

from neofoam_ui.parallel import rank_info
from neofoam_ui.toy.heat1d import Heat1D


class RankHeat(Heat1D):
    def create_context(self, results):
        ctx = super().create_context(results)
        ctx.fields["T"] += 100.0 * rank_info()[0]
        return ctx
