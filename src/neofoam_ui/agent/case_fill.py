# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NeoFOAM authors

"""Fill an ``incompressibleFluid`` case's config files with an LLM agent.

The aggregate ``CaseSpec`` model and its disk roundtrip live in
:mod:`neofoam.io.case_spec`; this module adds the agent on top:

- :func:`build_case_agent` — pydantic-ai ``Agent`` whose ``output_type`` is
  the aggregate; backed by Anthropic by default, falls back to the local
  Ollama scaffold via ``base_url``.
- :func:`fill_case` — one-shot: copy the on-disk mesh + ``0/``/``0.orig``
  fields from ``source_case`` to ``target_case``, then either disk-roundtrip
  the configs (no agent) or have an agent rewrite them.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel

from neofoam.io.case_spec import (
    build_case_output_model,
    load_case_from_disk,
    read_case_text,
    save_case,
)
from neofoam.solver.incompressibleFluid.incompressibleFluid import incompressibleFluid

try:
    from pydantic_ai import Agent
    from pydantic_ai.models.anthropic import AnthropicModel
except ModuleNotFoundError:  # optional [agent] extra
    Agent = None  # type: ignore[assignment,misc]
    AnthropicModel = None  # type: ignore[assignment,misc]

#: Actionable message when the optional ``[agent]`` extra is missing.
_MISSING_PYDANTIC_AI = (
    "the agent feature needs pydantic-ai; install with: pip install neofoam_ui[agent]"
)

__all__ = [
    "DEFAULT_CASE_SYSTEM_PROMPT",
    "build_case_agent",
    "fill_case",
]


DEFAULT_CASE_SYSTEM_PROMPT = (
    "You scaffold an OpenFOAM case for the ``incompressibleFluid`` solver."
    " Fill ONLY the configs needed for the user's case description; leave"
    " every other field as null. Always include ControlDictConfig,"
    " TransportPropertiesConfig and TurbulencePropertiesConfig. Include"
    " Pimple_fvSchemes / Pimple_fvSolution whenever PIMPLE pressure-velocity"
    " coupling is used (which is always, for this solver). Set Boussinesq"
    " configs only when the user explicitly asks for buoyancy."
)


def _solver() -> Any:
    """Return the incompressibleFluid solver spec."""
    return incompressibleFluid


def _copy_static_assets(source_case: Path, target_case: Path) -> None:
    """Mirror the parts of a case the agent can't (yet) generate.

    What we copy:

    - ``constant/polyMesh`` (or ``system/blockMeshDict`` if the mesh
      hasn't been built yet) and ``system/decomposeParDict``;
    - ``0/`` and ``0.orig/`` field dictionaries — boundary conditions are
      not part of the solver's config schema today;
    - ``system/fvSchemes`` / ``system/fvSolution`` — the per-spec config
      classes only model the entries the in-tree operations declared via
      ``@<Subclass>.add(...)``; writing from scratch would drop the rest.
      Mirroring then letting ``save_case`` patch in place keeps real
      cases (which have many more entries) working. Other configs
      (``controlDict``, ``transportProperties``, ``turbulenceProperties``)
      write cleanly from the schema alone now and don't need mirroring.

    Existing target files are removed before the copy.
    """
    target_case.mkdir(parents=True, exist_ok=True)
    for rel in (
        "constant/polyMesh",
        "system/blockMeshDict",
        "system/decomposeParDict",
        "system/fvSchemes",
        "system/fvSolution",
        "0",
        "0.orig",
    ):
        src = source_case / rel
        if not src.exists():
            continue
        dst = target_case / rel
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def build_case_agent(
    *,
    solver: Optional[Any] = None,
    model: Any = None,
    model_name: str = "claude-haiku-4-5",
    system_prompt: str = DEFAULT_CASE_SYSTEM_PROMPT,
    output_model_name: str = "CaseSpec",
    output_type: Optional[type[BaseModel]] = None,
    **agent_kwargs: Any,
) -> Any:
    """Build a pydantic-ai ``Agent`` returning a filled aggregate ``CaseSpec``.

    By default uses the Anthropic backend with ``claude-haiku-4-5``; pass a
    pre-built ``model`` to use the local-Ollama scaffold from
    :mod:`neofoam_ui.agent.agent` instead. Pass ``output_type`` to make the agent
    emit that model directly (e.g. a custom aggregate) instead of the synthesised
    per-solver ``CaseSpec``. Extra ``agent_kwargs`` are forwarded to
    :class:`pydantic_ai.Agent`.
    """
    if Agent is None:
        raise ImportError(_MISSING_PYDANTIC_AI)

    if model is None:
        if AnthropicModel is None:
            raise ImportError(_MISSING_PYDANTIC_AI)
        model = AnthropicModel(model_name)

    out_type = output_type or build_case_output_model(solver=solver, model_name=output_model_name)
    return Agent(
        model,
        output_type=out_type,
        system_prompt=system_prompt,
        **agent_kwargs,
    )


def _agent_prompt_from_source(source_case: Path, solver: Any) -> str:
    """Render the per-file source text into one prompt body."""
    files = read_case_text(source_case, solver=solver)
    parts = [
        "Fill the schema from these OpenFOAM dictionaries. Use the values"
        " present; leave irrelevant configs null.\n"
    ]
    for rel, text in files.items():
        if not text:
            continue
        parts.append(f"\n=== {rel} ===\n{text}")
    return "".join(parts)


def fill_case(
    source_case: Union[Path, str],
    target_case: Union[Path, str],
    *,
    agent: Any = None,
    solver: Optional[Any] = None,
    copy_static: bool = True,
) -> BaseModel:
    """Fill the configs of ``target_case`` from ``source_case``.

    The mesh, BC fields (``0/`` or ``0.orig/``) and decomposeParDict are
    mirrored from ``source_case`` (set ``copy_static=False`` to skip).
    Then:

    - if ``agent`` is provided, prompt it with the source-case text and use
      its validated ``CaseSpec`` to write the configs;
    - otherwise, load the configs from disk via :func:`load_case_from_disk`
      and write them out — a deterministic, LLM-free roundtrip used by
      tests and by the CLI's ``--no-llm`` mode.

    Returns the validated aggregate that was saved.

    A missing/invalid ``source_case`` raises :class:`ValueError` **before** any
    static-asset copy or agent call, so a bad source never burns an LLM API call on
    a contentless prompt (the guard the removed MCP ``fill_case`` tool used to carry).
    """
    solver = solver or _solver()
    source_case = Path(source_case)
    target_case = Path(target_case)

    if not source_case.is_dir():
        raise ValueError(f"source_case does not exist or is not a directory: {str(source_case)!r}")

    if copy_static:
        _copy_static_assets(source_case, target_case)

    if agent is None:
        case_spec = load_case_from_disk(source_case, solver=solver)
    else:
        prompt = _agent_prompt_from_source(source_case, solver)
        result = agent.run_sync(prompt)
        case_spec = result.output

    save_case(case_spec, target_case)
    return case_spec
