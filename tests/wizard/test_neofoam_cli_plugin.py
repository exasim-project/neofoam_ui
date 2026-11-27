# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NeoFOAM authors

"""``neofoam ui`` and ``neofoam agent`` come from this package's ``neofoam.cli`` entry points.

Runs ``neofoam``'s own CLI, so it needs this package installed (its entry points) next to
a ``neofoam`` that reads the ``neofoam.cli`` group.
"""

import pytest
from neofoam.cli.app import app
from typer.testing import CliRunner


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["ui", "--help"], ["wizard", "run"]),
        (["ui", "wizard", "--help"], ["--solver", "--port", "--no-browser"]),
        (["agent", "fill", "--help"], ["--no-llm", "--model"]),
    ],
)
def test_neofoam_cli_has_the_plugin_command(command: list[str], expected: list[str]) -> None:
    result = CliRunner().invoke(app, command, env={"NO_COLOR": "1", "COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    for word in expected:
        assert word in result.output, f"{word!r} missing from `neofoam {' '.join(command)}`"
