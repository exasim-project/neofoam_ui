# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NeoFOAM authors

"""NeoFOAM's limitations cases in the DAG stepper: ``--solver probe_ui:probe``.

The worker needs this directory on its PYTHONPATH and ``NEOFOAM_DIR`` set
(``scripts/probe.sh`` does both) and runs in the case directory.
"""

from typing import Any


def probe() -> Any:
    """multiRun of the case in the working directory, with the ``probe`` region solver."""
    import run  # noqa: PLC0415

    from neofoam_ui.adapters.neofoam import ObservedNeoFoamSolver  # noqa: PLC0415

    run.use_probe()
    # multiRun.run() takes no argv
    return ObservedNeoFoamSolver("multiRun", lambda argv: run.MULTI_RUN.run())
