"""Which MPI rank this process is (from the launcher's environment), and where rank 0 listens."""

from __future__ import annotations

import os

from neofoam_ui.protocol import SYNC_ENV

__all__ = ()

_RANK_ENV = (
    ("OMPI_COMM_WORLD_RANK", "OMPI_COMM_WORLD_SIZE"),
    ("PMIX_RANK", "PMIX_SIZE"),
    ("PMI_RANK", "PMI_SIZE"),
)


def rank_info() -> tuple[int, int]:
    """``(rank, size)`` of this process; ``(0, 1)`` outside MPI."""
    for rank, size in _RANK_ENV:
        if rank in os.environ and size in os.environ:
            return int(os.environ[rank]), int(os.environ[size])
    return 0, 1


def _address() -> tuple[str, int]:
    value = os.environ.get(SYNC_ENV)
    if not value:
        raise RuntimeError(f"an MPI run of the stepper needs {SYNC_ENV}=host:port")
    host, _, port = value.rpartition(":")
    return host or "127.0.0.1", int(port)
