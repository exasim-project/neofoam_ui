# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2025 NeoFOAM authors

"""pydantic-ai based AI agent scaffold for NeoFOAM.

Two layers live here:

- The generic scaffold (:mod:`neofoam_ui.agent.agent`) wires a pydantic-ai
  :class:`~pydantic_ai.Agent` to an OpenAI-compatible endpoint (local
  Ollama by default).
- The case-fill API (:mod:`neofoam_ui.agent.case_fill`) targets the
  ``incompressibleFluid`` solver: an agent fills the aggregate ``CaseSpec``
  of :mod:`neofoam.io.case_spec`, which is saved back via the registered IO
  strategies — so an agent (or the no-LLM disk-roundtrip path used by tests /
  the ``--no-llm`` CLI flag) can fill a full case from a source case or
  natural-language prompt.
"""

from neofoam_ui.agent.agent import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ExampleResponse,
    build_model,
    create_agent,
)
from neofoam_ui.agent.case_fill import (
    DEFAULT_CASE_SYSTEM_PROMPT,
    build_case_agent,
    fill_case,
)
from neofoam_ui.agent.case_forms import (
    INPUT_KEYS,
    field_name,
    is_scheme_config,
    merge_field_config,
    split_field_dump,
)

__all__ = [
    # generic scaffold
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "ExampleResponse",
    "build_model",
    "create_agent",
    # case-fill API
    "DEFAULT_CASE_SYSTEM_PROMPT",
    "build_case_agent",
    "fill_case",
    # case form wiring
    "INPUT_KEYS",
    "field_name",
    "is_scheme_config",
    "merge_field_config",
    "split_field_dump",
]
