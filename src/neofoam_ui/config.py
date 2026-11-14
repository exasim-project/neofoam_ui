"""Settings of the UI, its worker and Catalyst, read from YAML config files.

Two files, both optional: the user's ``$XDG_CONFIG_HOME/neofoam_ui/config.yaml`` (default
``~/.config/neofoam_ui/config.yaml``) for the machine, and the case's
``system/neofoam_ui.yaml`` for one case. Precedence: command line (click, the files are its
defaults) > case file > user file > built-in default. An unknown section or key, or a value of the wrong type, is an error naming
the file and the key.

Use :func:`load_config` wherever a setting is needed: the UI (``neofoam-ui``), the worker
(``python -m neofoam_ui.worker``, run in the case directory) and a solver's own in-situ entry
read the same files (so a worker's interpreter needs PyYAML and pydantic). The models are
frozen. ``~`` in a path value (``worker.python``, ``catalyst.prefix``/``script``/``out``)
is expanded; a relative path stays relative to the reading process's working directory (the
case directory for the worker).

Example (``~/.config/neofoam_ui/config.yaml``)::

    worker:
      python: ~/NeoFOAM/.venv/bin/python
      np: 2
    catalyst:
      prefix: /opt/paraview-catalyst
      live: localhost:22222   # a desktop ParaView attaches with Catalyst ▸ Connect
    ui:
      port: 8080

    >>> load_config("run/pitzDaily").catalyst.prefix
    '/opt/paraview-catalyst'
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Optional

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

__all__ = ["CatalystConfig", "Config", "UiConfig", "WorkerConfig", "load_config"]

#: The case's config file, relative to the case directory.
_CASE_FILE = Path("system") / "neofoam_ui.yaml"


def _expand_user(path: Optional[str]) -> Optional[str]:
    return None if path is None else os.path.expanduser(path)


#: A file or directory: ``~`` expanded, nothing else (no ``$VARS``).
_Path = Annotated[Optional[str], AfterValidator(_expand_user)]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkerConfig(_Section):
    """How the UI starts the solver worker."""

    #: Interpreter of the worker, e.g. a NeoFOAM venv (``None``: the UI's own).
    python: _Path = None
    #: MPI ranks; > 1 runs the worker under ``mpirun``.
    np: int = 1
    #: MPI launcher command (split like a shell would).
    mpirun: str = "mpirun"
    #: Address rank 0 listens on for the other ranks.
    sync_host: str = "127.0.0.1"


class CatalystConfig(_Section):
    """Catalyst 2 + ParaView for the in-situ field view."""

    #: Install prefix (``lib/libcatalyst.so``, ParaView's Python packages); ``None``: no field view.
    prefix: _Path = None
    #: Another pipeline script than ``pipelines/view.py`` (it gets every cell field).
    script: _Path = None
    #: Where a solver's own in-situ entry writes its images (default ``postProcessing/<entry>``).
    out: _Path = None
    #: ``host:port`` of a desktop ParaView's Catalyst Live (``None``: none).
    live: Optional[str] = None


class UiConfig(_Section):
    """The UI process."""

    #: Pause after every event while running, in ms.
    delay_ms: float = 0.0
    #: The solver waits until the UI showed every event and frame.
    wait_ui: bool = False
    host: str = "localhost"
    port: int = 8080


class Config(_Section):
    """All settings, by section."""

    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    catalyst: CatalystConfig = Field(default_factory=CatalystConfig)
    ui: UiConfig = Field(default_factory=UiConfig)


def load_config(case: str | os.PathLike[str] = ".") -> Config:
    """The settings for ``case``: the user file, then the case file over it.

    The command lines (``neofoam-ui``, ``python -m neofoam_ui.worker``) take these as their
    defaults and check the final values with the same models."""
    merged: dict[str, dict[str, Any]] = {}
    for path in (_user_file(), Path(case) / _CASE_FILE):
        _merge(merged, _read(path), str(path))
    return Config.model_validate(merged)


def _user_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "neofoam_ui" / "config.yaml"


def _read(path: Path) -> Any:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _merge(into: dict[str, dict[str, Any]], data: Any, source: str) -> None:
    """Check ``data`` (one file's content) and lay it over ``into``."""
    if not isinstance(data, dict):
        raise ValueError(f"{source}: expected sections ({', '.join(Config.model_fields)}), "
                         f"got {type(data).__name__}")
    try:
        Config.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"{source}: {_describe(exc.errors()[0])}") from exc
    for name, values in data.items():
        into.setdefault(name, {}).update(values)


def _describe(error: Any) -> str:
    """One pydantic error as ``unknown key worker.x`` or ``worker.np: <message>, got <value>``."""
    key = ".".join(str(part) for part in error["loc"])
    if error["type"] == "extra_forbidden":
        return f"unknown section {key!r}" if len(error["loc"]) == 1 else f"unknown key {key}"
    return f"{key}: {error['msg']}, got {error['input']!r}"
