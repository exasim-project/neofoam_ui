"""``neofoam ui``: the group ``neofoam`` adds through the ``neofoam.cli`` entry point.

Tested on the Typer group itself, so no NeoFOAM is needed; ``neofoam``'s own tests cover
how it adds entry-point groups.
"""

from typer.testing import CliRunner

from neofoam_ui.cli import app


def test_ui_run_forwards_its_arguments_to_the_stepper():
    result = CliRunner().invoke(app, ["run", "--help"])

    assert result.exit_code == 0, result.output
    assert "Usage: neofoam ui run" in result.output
    assert "--solver" in result.output


def test_ui_run_reports_a_bad_stepper_option():
    result = CliRunner().invoke(app, ["run", "--no-such-option"])

    assert result.exit_code == 2
    assert "No such option '--no-such-option'" in result.output
