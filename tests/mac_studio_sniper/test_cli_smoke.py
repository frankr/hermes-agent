"""Non-browser CLI smoke tests: argument wiring and gate-mapped commands."""

from pathlib import Path

from mac_studio_sniper.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[2] / "mac_studio_sniper"


def test_parse_command_exit_zero(capsys):
    rc = main(["parse", "--html", str(FIXTURES / "grid_sample.html")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "tiles: 4" in out


def test_targets_command(capsys):
    rc = main(
        [
            "targets",
            "--html",
            str(FIXTURES / "grid_sample.html"),
            "--targets",
            str(REPO / "targets.yaml"),
        ]
    )
    assert rc == 0
    assert "matches:" in capsys.readouterr().out


def test_flightplan_command_reports_unverified(capsys):
    rc = main(["flightplan", "--flightplan", str(REPO / "flightplan.yaml")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verified: False" in out
    assert "NOT verified" in out


def test_inject_then_status(tmp_path, capsys):
    rc = main(["--state-dir", str(tmp_path), "inject", "--price", "5000", "--ram", "512"])
    assert rc == 0
    assert (tmp_path / "inject").glob("*.json")
    capsys.readouterr()
    rc = main(["--state-dir", str(tmp_path), "status"])
    assert rc == 0


def test_learn_windows_command(tmp_path, capsys):
    # Empty DB → falls back to default window.
    from mac_studio_sniper.state import StateDB

    StateDB(tmp_path / "state.sqlite")
    rc = main(["--state-dir", str(tmp_path), "learn-windows"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "hot windows" in out
