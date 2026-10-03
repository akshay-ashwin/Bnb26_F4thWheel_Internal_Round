import pytest

from sim import __version__
from sim.cli import main


def test_main_prints_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert __version__ in capsys.readouterr().out
