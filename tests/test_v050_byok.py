"""v0.5.0: bring-your-own-key core, key safety, and the no-secret-on-disk rules.

Three groups of tests:

* **M2, provider-neutral core** — a fresh install has no default provider, judge,
  model or price; the headless ``connections.yaml`` path names an environment
  variable per key and is parsed before anything is called; there is no
  ``--api-key`` argument anywhere; a live CLI run needs ``--connections`` plus
  ``--confirm-live`` and prints the plan and worst-case cost first.
* **M3, key safety** — the key goes only to the configured host (a redirect is
  refused, not followed), base URLs with embedded credentials are refused, the
  app refuses a non-loopback bind without ``--allow-remote``, and ``doctor``
  warns about local ``.env`` files, an off-loopback bind and ``--use-env``.
* **M4, guardrails** — presets ship no prices, a missing price says "cost
  unavailable, call cap is binding", and the app asks for a connection instead
  of choosing a model for the user.

Nothing here opens, reads or writes a real ``.env``; the only ``.env``-style file
is an empty one created inside ``tmp_path`` to prove the doctor warning works.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medeval.cli import build_parser, main
from medeval_core.client import OpenAICompatibleTransport
from medeval_study import mock_bindings
from medeval_study.connections_file import (
    ConnectionsFileError,
    load_connections_file,
)
from medeval_web.app import RunManager, create_app
from medeval_web.connections import PRESETS, Connection, base_url_problems, base_url_warnings

from .fake_openai_server import FakeOpenAIServer
from .support import ROOT

#: An obviously fake key. Never a credential; every expectation below is that it
#: does not appear in output.
FAKE_KEY = "sk-test-v050-should-never-be-printed-0000"
TWO_JUDGES = """\
connections:
  - name: "judge A"
    base_url: "https://judge-a.example.com/v1"
    model: "model-a"
    key_env: "MEDEVAL_TEST_KEY_A"
    family_label: "family-a"
    call_cap: 100
    usd_cap: 1.0
  - name: "judge B"
    base_url: "https://judge-b.example.com/v1"
    model: "model-b"
    key_env: "MEDEVAL_TEST_KEY_B"
    family_label: "family-b"
    call_cap: 100
    usd_cap: 1.0
"""


def write_connections(tmp_path: Path, body: str, name: str = "connections.yaml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# M2: connections.yaml (headless bring-your-own-key)
# --------------------------------------------------------------------------- #
def test_example_connections_file_parses_and_has_no_key() -> None:
    example = ROOT / "connections.example.yaml"
    text = example.read_text(encoding="utf-8")
    active = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
    assert "api_key" not in active
    assert "key:" not in active.replace("key_env:", "")
    parsed = load_connections_file(example)
    assert len(parsed.judges) == 2
    assert parsed.problems() == []
    # Keys are named by environment variable, never embedded.
    assert all(row.key_env for row in parsed.judges)


def test_connections_file_refuses_a_key_field(tmp_path: Path) -> None:
    path = write_connections(
        tmp_path,
        TWO_JUDGES.replace('key_env: "MEDEVAL_TEST_KEY_A"', f'key: "{FAKE_KEY}"'),
    )
    with pytest.raises(ConnectionsFileError) as excinfo:
        load_connections_file(path)
    message = str(excinfo.value)
    assert "key_env" in message
    assert FAKE_KEY not in message, "an error message must never echo the secret it refused"


def test_connections_file_refuses_credentials_in_base_url(tmp_path: Path) -> None:
    path = write_connections(
        tmp_path,
        TWO_JUDGES.replace("https://judge-a.example.com/v1", "https://user:pass@judge-a.example.com/v1"),
    )
    with pytest.raises(ConnectionsFileError) as excinfo:
        load_connections_file(path)
    assert "must not embed credentials" in str(excinfo.value)


def test_connections_file_field_rules(tmp_path: Path) -> None:
    cases = [
        ("ftp://judge-a.example.com/v1", "http:// or https://"),
        ('    surprise: 1\n', "unknown field"),
        ('    model: ""\n', "model is required"),
    ]
    for index, (fragment, expected) in enumerate(cases):
        if fragment.startswith("ftp://"):
            body = TWO_JUDGES.replace("https://judge-a.example.com/v1", fragment)
        else:
            body = TWO_JUDGES.replace("    key_env: \"MEDEVAL_TEST_KEY_A\"\n", fragment)
        with pytest.raises(ConnectionsFileError) as excinfo:
            load_connections_file(write_connections(tmp_path, body, name=f"case-{index}.yaml"))
        assert expected in str(excinfo.value), (fragment, str(excinfo.value))


def test_connections_file_needs_key_env_only_for_remote_hosts(tmp_path: Path) -> None:
    remote = write_connections(
        tmp_path, TWO_JUDGES.replace('    key_env: "MEDEVAL_TEST_KEY_A"\n', ""), name="remote.yaml"
    )
    with pytest.raises(ConnectionsFileError) as excinfo:
        load_connections_file(remote)
    assert "key_env is required" in str(excinfo.value)

    loopback = write_connections(
        tmp_path,
        """\
connections:
  - name: "local model"
    base_url: "http://127.0.0.1:9999/v1"
    model: "some-local-model"
    family_label: "local"
""",
        name="local.yaml",
    )
    parsed = load_connections_file(loopback)
    assert parsed.judges[0].key(environ={}) is None
    assert parsed.missing_keys(environ={}) == []


def test_missing_keys_names_variables_never_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = load_connections_file(write_connections(tmp_path, TWO_JUDGES))
    monkeypatch.setenv("MEDEVAL_TEST_KEY_A", FAKE_KEY)
    monkeypatch.delenv("MEDEVAL_TEST_KEY_B", raising=False)
    assert parsed.missing_keys() == ["MEDEVAL_TEST_KEY_B"]
    bindings = parsed.judge_bindings()
    assert bindings[0]["api_key"] == FAKE_KEY
    assert bindings[1]["api_key"] is None


def test_screening_role_rules(tmp_path: Path) -> None:
    same_family = write_connections(
        tmp_path,
        TWO_JUDGES
        + """\
  - name: "screening model"
    base_url: "https://screening.example.com/v1"
    model: "model-s"
    key_env: "MEDEVAL_TEST_KEY_S"
    family_label: "family-a"
    role: screening
""",
        name="same-family.yaml",
    )
    with pytest.raises(ConnectionsFileError) as excinfo:
        load_connections_file(same_family)
    assert "same family" in str(excinfo.value)

    two_screeners = write_connections(
        tmp_path,
        """\
connections:
  - name: "judge"
    base_url: "https://a.example.com/v1"
    model: "m"
    key_env: "MEDEVAL_TEST_KEY_A"
  - name: "screen one"
    base_url: "https://b.example.com/v1"
    model: "m"
    key_env: "MEDEVAL_TEST_KEY_B"
    role: screening
  - name: "screen two"
    base_url: "https://c.example.com/v1"
    model: "m"
    key_env: "MEDEVAL_TEST_KEY_C"
    role: screening
""",
        name="two-screeners.yaml",
    )
    with pytest.raises(ConnectionsFileError) as excinfo:
        load_connections_file(two_screeners)
    assert "at most one" in str(excinfo.value)


PRICED_JUDGES = """\
connections:
  - name: "judge A"
    base_url: "https://judge-a.example.com/v1"
    model: "model-a"
    key_env: "MEDEVAL_TEST_KEY_A"
    family_label: "family-a"
    call_cap: 100
    usd_cap: 1.0
    price_in_per_mtok: 1.0
    price_out_per_mtok: 2.0
  - name: "judge B"
    base_url: "https://judge-b.example.com/v1"
    model: "model-b"
    key_env: "MEDEVAL_TEST_KEY_B"
    family_label: "family-b"
    call_cap: 100
    usd_cap: 1.0
    price_in_per_mtok: 1.0
    price_out_per_mtok: 2.0
"""


def test_worst_case_cost_is_none_without_prices_and_bounded_with_them(tmp_path: Path) -> None:
    parsed = load_connections_file(write_connections(tmp_path, TWO_JUDGES))
    assert parsed.worst_case_usd(n_items=10, repeats=1) is None
    assert parsed.required_calls_for(n_items=10, repeats=2) == 40

    priced = load_connections_file(write_connections(tmp_path, PRICED_JUDGES, name="priced.yaml"))
    worst = priced.worst_case_usd(n_items=10, repeats=1)
    assert worst is not None and worst > 0
    # 20 calls total (10 items x 1 repeat x 2 judges), 1200 input tokens and
    # max_tokens 2048 output per call, at $1/Mtok in and $2/Mtok out.
    assert worst == pytest.approx(20 * (1200 * 1.0 + 2048 * 2.0) / 1_000_000, rel=1e-6)


# --------------------------------------------------------------------------- #
# M2: the CLI is provider-neutral and never takes a key on the command line
# --------------------------------------------------------------------------- #
def _walk_parsers(parser: argparse.ArgumentParser) -> list[argparse.ArgumentParser]:
    found = [parser]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                found.extend(_walk_parsers(sub))
    return found


def test_no_api_key_command_line_argument_anywhere() -> None:
    parser = build_parser()
    for sub in _walk_parsers(parser):
        for action in sub._actions:
            assert "--api-key" not in (action.option_strings or []), f"{sub.prog} must not accept --api-key"
    with pytest.raises(SystemExit):
        parser.parse_args(["ui", "--api-key", FAKE_KEY])
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "exp002", "--api-key", FAKE_KEY])


def test_live_cli_run_without_connections_explains_both_paths(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "exp002", "--n-items", "2"])
    out = capsys.readouterr().out
    assert code == 2
    assert "connections.example.yaml" in out
    assert "--connections" in out
    assert "Models page" in out
    assert "dry run needs no key" in out.lower()


def test_live_cli_run_reports_unset_variables_without_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_connections(tmp_path, TWO_JUDGES)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_A", FAKE_KEY)
    monkeypatch.delenv("MEDEVAL_TEST_KEY_B", raising=False)
    code = main(["run", "exp002", "--connections", str(path), "--n-items", "2"])
    out = capsys.readouterr().out
    assert code == 2
    assert "MEDEVAL_TEST_KEY_B" in out
    assert FAKE_KEY not in out


def test_live_cli_prints_the_plan_and_calls_nothing_without_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_connections(tmp_path, TWO_JUDGES)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_A", FAKE_KEY)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_B", FAKE_KEY)
    code = main(["run", "exp002", "--connections", str(path), "--n-items", "4", "--repeats", "1"])
    out = capsys.readouterr().out
    assert code == 2
    assert "plan: 4 item(s) x 1 repeat(s) x 2 judge(s) = 8 judge call(s)" in out
    # No prices declared => the estimate must say so instead of inventing one.
    assert "unavailable, call cap is binding" in out
    assert "nothing was called" in out
    assert FAKE_KEY not in out


def test_live_cli_refuses_a_plan_over_the_connection_call_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_connections(tmp_path, TWO_JUDGES)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_A", FAKE_KEY)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_B", FAKE_KEY)
    code = main(["run", "exp002", "--connections", str(path), "--n-items", "150", "--repeats", "1"])
    out = capsys.readouterr().out
    assert code == 2
    assert "call_cap is 100" in out
    assert "nothing was called" in out


def test_live_cli_refuses_a_plan_over_the_run_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = TWO_JUDGES.replace("call_cap: 100", "call_cap: 1000")
    path = write_connections(tmp_path, body)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_A", FAKE_KEY)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_B", FAKE_KEY)
    code = main(
        ["run", "exp002", "--connections", str(path), "--n-items", "10", "--repeats", "1", "--max-api-calls", "5"]
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "above the run cap of 5" in out


class _CapturedRun:
    """Stands in for StudyRun/Exp003Study so the CLI can be tested with no run."""

    last_config: Any = None

    def __init__(self, config: Any, settings: Any, **kwargs: Any) -> None:
        type(self).last_config = config

    def execute(self) -> dict[str, Any]:
        return {"status": "completed", "run_dir": "tmp", "run_id": "captured", "metrics": {}, "usage": {}}


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> type[_CapturedRun]:
    import medeval_study

    _CapturedRun.last_config = None
    monkeypatch.setattr(medeval_study, "StudyRun", _CapturedRun)
    import medeval_study.exp003 as exp003_module

    monkeypatch.setattr(exp003_module, "Exp003Study", _CapturedRun)
    return _CapturedRun


def test_dry_run_without_connections_uses_labelled_mock_judges(
    captured: type[_CapturedRun], capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run", "exp002", "--dry-run", "--n-items", "2", "--repeats", "1"])
    config = captured.last_config
    capsys.readouterr()
    assert code == 0
    assert config.judges == ()
    assert [binding["judge_id"] for binding in config.connection_judges] == ["judge_one", "judge_two"]
    assert all(binding["backend"] == "mock" for binding in config.connection_judges)
    assert config.connection_judges == mock_bindings.dry_run_judge_bindings(1)


def test_confirmed_live_run_builds_bindings_from_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: type[_CapturedRun], capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_connections(tmp_path, TWO_JUDGES)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_A", FAKE_KEY)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_B", FAKE_KEY)
    code = main(
        [
            "run", "exp002", "--connections", str(path), "--confirm-live",
            "--n-items", "1", "--repeats", "1", "--max-api-calls", "100",
        ]
    )
    out = capsys.readouterr().out
    config = captured.last_config
    assert code == 0
    assert config.dry_run is False
    assert config.judges == ()
    assert [binding["judge_id"] for binding in config.connection_judges] == ["judge-a", "judge-b"]
    assert config.connection_judges[0]["base_url"] == "https://judge-a.example.com/v1"
    # The key travels in the binding (process memory) and never in the serialized config.
    assert config.connection_judges[0]["api_key"] == FAKE_KEY
    assert FAKE_KEY not in str(config.as_dict())
    assert FAKE_KEY not in out


def test_exp003_live_run_needs_a_screening_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_connections(tmp_path, TWO_JUDGES)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_A", FAKE_KEY)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_B", FAKE_KEY)
    code = main(["run", "exp003", "--connections", str(path), "--confirm-live"])
    out = capsys.readouterr().out
    assert code == 2
    assert "screening connection" in out


def test_max_usd_flag_overrides_the_environment_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: type[_CapturedRun], capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_connections(tmp_path, PRICED_JUDGES)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_A", FAKE_KEY)
    monkeypatch.setenv("MEDEVAL_TEST_KEY_B", "sk-test-second-value-1111")
    monkeypatch.setenv("MEDEVAL_MAX_USD", "100")
    code = main(
        ["run", "exp002", "--connections", str(path), "--confirm-live", "--n-items", "1", "--repeats", "1"]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "worst-case cost: US$" in out
    assert "run-wide caps: 500 call(s), US$100.00" in out


# --------------------------------------------------------------------------- #
# M3: the key goes only to the configured host
# --------------------------------------------------------------------------- #
@pytest.mark.allow_loopback
def test_redirect_to_another_host_is_refused_and_never_receives_the_key() -> None:
    key = "sk-test-redirect-should-not-travel-42"
    with FakeOpenAIServer(mode="verdicts", expected_key=key) as second:
        with FakeOpenAIServer(mode="redirect", redirect_target=f"{second.base_url}/chat/completions") as first:
            transport = OpenAICompatibleTransport(
                base_url=first.base_url, model="m", api_key=key, max_attempts=3
            )
            result = transport.complete(
                [{"role": "user", "content": "hello"}],
                temperature=0.0,
                seed=1,
                max_tokens=8,
                timeout_s=5.0,
            )
    assert result.error_type == "redirect"
    assert result.attempts == 1, "a redirect must never be retried"
    assert second.requests == [], "the second host must never be contacted"
    assert key not in (result.error or "")
    assert "refusing to follow" in (result.error or "")


def test_base_url_rules_refuse_credentials_and_warn_about_plain_http() -> None:
    assert base_url_problems("https://user:pass@api.example.com/v1")
    assert any("credentials" in problem for problem in base_url_problems("https://user:pass@api.example.com/v1"))
    assert base_url_problems("ftp://api.example.com/v1")
    assert base_url_warnings("http://api.example.com/v1"), "plain http to a remote host must warn"
    assert base_url_warnings("http://127.0.0.1:11434/v1") == []
    assert base_url_warnings("https://api.example.com/v1") == []
    connection = Connection(
        id="c1", name="n", provider_preset="custom", base_url="https://user:pass@api.example.com/v1",
        model="m",
    )
    assert any("credentials" in problem for problem in connection.validate())


def test_presets_ship_no_prices_and_carry_verification_metadata() -> None:
    for key, preset in PRESETS.items():
        assert "price_in_per_mtok" not in preset
        assert "price_out_per_mtok" not in preset
        assert "pricing_url" in preset
        assert "last_verified" in preset
        if key != "custom":
            assert preset["pricing_url"] or key == "ollama"


def test_ui_refuses_a_non_loopback_bind_without_allow_remote(capsys: pytest.CaptureFixture[str]) -> None:
    from medeval_web.server import serve

    code = serve(host="0.0.0.0", port=8799, open_browser=False)
    out = capsys.readouterr().out
    assert code == 2
    assert "loopback-only" in out
    assert "--allow-remote" in out


def test_ui_warns_loudly_when_allow_remote_is_used(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The bind decision and the warning, without starting a real server."""
    import uvicorn

    from medeval_web import server as server_module

    class _FakeKeyStore:
        def forget_all(self) -> int:
            return 0

    class _FakeApp:
        state = type("S", (), {"key_store": _FakeKeyStore()})()

    monkeypatch.setattr(server_module, "pick_port", lambda host, port: port)
    monkeypatch.setattr("medeval_web.app.create_app", lambda **kwargs: _FakeApp())
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    code = server_module.serve(host="0.0.0.0", port=8799, open_browser=False, allow_remote=True)
    out = capsys.readouterr().out
    assert code == 0
    assert "WARNING" in out
    assert "NOT loopback" in out
    assert "do not expose" in out.lower()


def test_doctor_warns_about_env_files_offloopback_bind_and_use_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from medeval_web import doctor as doctor_module

    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "results").mkdir()
    (root / ".env.local").write_text("", encoding="utf-8")  # empty: no real key is created or read
    monkeypatch.setattr(doctor_module, "repo_root", lambda: root)
    monkeypatch.setattr(doctor_module, "data_dir", lambda: root / "data")
    monkeypatch.setattr(doctor_module, "results_dir", lambda: root / "results")

    doctor_module.run_doctor(port=0, host="0.0.0.0", use_env=True)
    out = capsys.readouterr().out
    assert "local .env files" in out
    assert ".env.local" in out
    assert "--use-env" in out
    assert "bind address" in out
    assert "not loopback" in out


# --------------------------------------------------------------------------- #
# M4: the app asks for a connection instead of choosing a provider
# --------------------------------------------------------------------------- #
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    root = tmp_path / "study_runs"
    root.mkdir(exist_ok=True)
    from medeval_study import runner as runner_module
    from medeval_web import app as app_module

    monkeypatch.setattr(app_module, "run_root", lambda: root)
    monkeypatch.setattr(runner_module, "default_run_root", lambda: root)
    return TestClient(create_app(RunManager(), local_data_dir=tmp_path / "local-data"))


def test_presets_endpoint_ships_no_price_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = app_client(tmp_path, monkeypatch)
    payload = client.get("/api/presets").json()
    assert payload["presets"]
    for preset in payload["presets"]:
        assert preset["prices_default"] is None
        assert "pricing_url" in preset
        assert "last_verified" in preset


def test_live_run_without_a_connection_is_refused_not_given_a_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app_client(tmp_path, monkeypatch)
    started = client.post(
        "/api/runs",
        json={"experiment": "exp002", "dry_run": False, "confirm_real_calls": True, "n_items": 2, "repeats": 1},
    )
    assert started.status_code == 400
    assert "needs judges" in started.json()["detail"]
    assert "Models page" in started.json()["detail"]


def test_estimate_says_the_call_cap_binds_when_no_price_is_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app_client(tmp_path, monkeypatch)
    created = client.post(
        "/api/connections",
        json={
            "name": "unpriced",
            "provider_preset": "custom",
            "base_url": "https://unpriced.example.com/v1",
            "model": "model-x",
            "family_label": "family-x",
            "key": FAKE_KEY,
        },
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["id"]
    assert client.post("/api/roles", json={"judges": [{"role": "judge_1", "connection_id": connection_id}]}).status_code == 200

    estimate = client.get("/api/estimate", params={"n_items": 2, "repeats": 1, "dry_run": "true"})
    assert estimate.status_code == 200, estimate.text
    plan = estimate.json()["connection_plan"]
    assert plan["worst_case_usd"] is None
    assert "call cap is binding" in plan["note"]
    assert FAKE_KEY not in estimate.text


def test_new_run_code_names_no_provider() -> None:
    """No default judge ids or provider env names left in new-run code."""
    app_js = (ROOT / "packages" / "medeval_web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "j1_deepseek" not in app_js
    assert "j2_ollama" not in app_js
    assert "cannot see, stop or refund" in app_js, "the first-run notice must say who pays"
    for relative in (
        "packages/medeval/cli.py",
        "packages/medeval_web/app.py",
        "packages/medeval_study/mock_bindings.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "DEEPSEEK_API_KEY" not in text, relative
