from typer.testing import CliRunner

from recallzero import __version__
from recallzero.cli import app


def test_cli_version_option() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"RecallZero {__version__}" in result.stdout
