"""CLI surface for Paper Packs, matrix runs and offline regrading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medeval.cli import main


def run_cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_paper_list_and_validate(capsys: pytest.CaptureFixture[str]) -> None:
    code, output = run_cli(capsys, "paper", "list")
    assert code == 0
    assert "template-clinical-mcq" in output
    code, output = run_cli(capsys, "paper", "validate")
    assert code == 0
    assert "VALID" in output


def test_paper_init_then_validate(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    destination = tmp_path / "cli-starter"
    code, output = run_cli(capsys, "paper", "init", "cli-starter", "--task", "mcq", "--dest", str(destination))
    assert code == 0, output
    assert (destination / "paper.yaml").is_file()
    code, output = run_cli(capsys, "paper", "validate", str(destination))
    assert code == 0, output
    assert "VALID" in output


def test_matrix_run_show_and_regrade(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEDEVAL_RESULTS_DIR", str(tmp_path / "results"))
    code, output = run_cli(
        capsys,
        "matrix", "run", "--paper", "template-clinical-mcq", "--dry-run",
        "--n-items", "4", "--max-calls", "20", "--mock-targets", "2",
    )
    assert code == 0, output
    assert "status: completed" in output
    run_dir = next(line.split("run directory:", 1)[1].strip() for line in output.splitlines() if "run directory:" in line)
    run_id = Path(run_dir).name

    code, output = run_cli(capsys, "matrix", "show", run_id)
    assert code == 0, output
    assert "PIPELINE TEST ONLY" in output

    code, output = run_cli(capsys, "regrade", run_id, "--paper", "template-clinical-mcq")
    assert code == 0, output
    assert "target calls: 0" in output
    assert "report:" in output


def test_matrix_live_without_connections_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code, output = run_cli(capsys, "matrix", "run", "--paper", "template-clinical-mcq", "--confirm-live")
    assert code == 2
    assert "needs --connections" in output


def test_connections_api_key_env_alias_and_target_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from medeval_study.connections_file import load_connections_file

    monkeypatch.setenv("MY_TARGET_KEY", "sk-alias-secret")
    path = tmp_path / "connections.local.yaml"
    path.write_text(
        "connections:\n"
        "  - name: target one\n"
        "    base_url: https://target.example.com/v1\n"
        "    model: target-model\n"
        "    role: target\n"
        "    api_key_env: MY_TARGET_KEY\n"
        "  - name: judge one\n"
        "    base_url: https://judge.example.com/v1\n"
        "    model: judge-model\n"
        "    role: judge\n"
        "    key_env: MY_TARGET_KEY\n",
        encoding="utf-8",
    )
    parsed = load_connections_file(path)
    assert parsed.targets and parsed.judges
    assert parsed.targets[0].key() == "sk-alias-secret"
    bindings = parsed.target_bindings()
    assert bindings[0]["role"] == "target"
    assert bindings[0]["api_key"] == "sk-alias-secret"
    assert "sk-alias-secret" not in json.dumps([row.summary() for row in parsed.connections])


def test_plaintext_key_is_still_refused(tmp_path: Path) -> None:
    from medeval_study.connections_file import ConnectionsFileError, load_connections_file

    path = tmp_path / "bad.yaml"
    path.write_text(
        "connections:\n"
        "  - name: leaky\n"
        "    base_url: https://example.com/v1\n"
        "    model: m\n"
        "    role: target\n"
        "    key: sk-not-allowed\n",
        encoding="utf-8",
    )
    with pytest.raises(ConnectionsFileError) as excinfo:
        load_connections_file(path)
    assert "key_env" in str(excinfo.value)
    assert "sk-not-allowed" not in str(excinfo.value)
