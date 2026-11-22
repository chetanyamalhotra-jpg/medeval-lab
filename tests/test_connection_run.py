"""Connection-backed runs: N judges, redaction, caps, consistency subsample.

Every test here runs the real runner against a local fake OpenAI-compatible
server (``fake_openai_server.py``) or the mock transport — no network beyond the
loopback, no spend, and no benchmark download (the loader is monkeypatched).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from medeval_core.config import Settings
from medeval_study import runner as runner_module
from medeval_study.datasets import DatasetBundle, DatasetLicence
from medeval_study.models import McqItem
from medeval_study.runner import JudgeBinding, StudyConfig, StudyRun, rebuild_run
from tests.fake_openai_server import FakeOpenAIServer
from tests.support import ROOT

pytestmark = pytest.mark.allow_loopback
sys.path.insert(0, str(ROOT / "tests"))

FAKE_KEY = "sk-fake-key-for-redaction-tests-4242"
OPTIONS = {"A": "alpha", "B": "beta", "C": "gamma", "D": "delta"}


def make_item(index: int) -> McqItem:
    return McqItem(
        item_id=f"syn-{index:03d}",
        question=(
            f"Synthetic vignette number {index}: a patient presents with a presentation that needs a "
            "single best answer, and the stem is long enough to be accepted by the loader."
        ),
        options=dict(OPTIONS),
        key_letter="ABCD"[index % 4],
        key_text=OPTIONS["ABCD"[index % 4]],
        dataset="synthetic",
        split="dev",
        row_index=index,
        meta={},
    )


def bundle(n: int = 6) -> DatasetBundle:
    licence = DatasetLicence(
        key="synthetic", hf_repo="none/synthetic", licence="synthetic-test",
        licence_evidence="fixture", redistribute=True, attribution="fixture",
    )
    return DatasetBundle(
        items=tuple(make_item(index) for index in range(n)),
        licence=licence, dataset="synthetic", split="dev", strategy="test",
        source_detail="fixture", n_raw_rows=n, n_dropped=0,
    )


def settings() -> Settings:
    return Settings(
        base_url=None, model=None, api_key=None,
        max_tokens=64, temperature=0.3, seed=7, retries=1,
        max_calls=100, max_usd=1.0,
        input_usd_per_million=0.30, output_usd_per_million=1.20, price_source="test-prices",
    )


def binding(judge_id: str, connection_id: str, server_url: str, **overrides) -> dict:
    payload = JudgeBinding(
        judge_id=judge_id,
        connection_id=connection_id,
        family_label=overrides.pop("family_label", judge_id),
        backend="api",
        base_url=server_url,
        model=f"fake-model-{judge_id}",
        api_key=overrides.pop("api_key", FAKE_KEY),
        temperature=overrides.pop("temperature", 0.3),
        repeats=overrides.pop("repeats", 1),
        max_tokens=overrides.pop("max_tokens", 64),
        call_cap=overrides.pop("call_cap", 500),
        usd_cap=overrides.pop("usd_cap", 5.0),
        price_in_per_mtok=0.30,
        price_out_per_mtok=1.20,
    ).as_dict(include_key=True)
    payload.update(overrides)
    return payload


@pytest.fixture()
def patched_loader(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner_module, "dataset_cache_status", lambda *args, **kwargs: bundle())


def run_study(run_id: str, tmp_path: Path, config: StudyConfig) -> dict:
    return StudyRun(config, settings(), run_root=tmp_path).execute()


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def _assert_key_nowhere(run_dir: Path, key: str, capsys: pytest.CaptureFixture[str]) -> None:
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert key not in path.read_text(encoding="utf-8", errors="ignore"), f"key leaked into {path.name}"
    captured = capsys.readouterr()
    assert key not in captured.out
    assert key not in captured.err


def test_forced_error_run_redacts_the_key_everywhere(patched_loader, tmp_path: Path, capsys) -> None:
    with FakeOpenAIServer(mode="http_500", expected_key=FAKE_KEY) as server:
        config = StudyConfig(
            dataset="synthetic", n_items=2, repeats=1, dry_run=False,
            connection_judges=(
                binding("judge_deepseek", "conn-1", server.base_url),
                binding("judge_ollama", "conn-2", server.base_url),
            ),
            max_api_calls=100, run_id="forced-error", resume=False,
        )
        result = run_study("forced-error", tmp_path, config)
    assert result["status"] == "completed", "API errors are recorded per item, not fatal"
    metrics = json.loads((tmp_path / "forced-error" / "metrics.json").read_text(encoding="utf-8"))
    for judge in metrics["judges"].values():
        assert judge["primary_repeat"]["n_api_errors"] > 0
        assert judge["primary_repeat"]["failure_counts"]["api_error"] > 0
    _assert_key_nowhere(tmp_path / "forced-error", FAKE_KEY, capsys)


def test_dry_run_with_connection_keys_redacts_everywhere(patched_loader, tmp_path: Path, capsys) -> None:
    config = StudyConfig(
        dataset="synthetic", n_items=2, repeats=1, dry_run=True,
        connection_judges=(
            binding("judge_one", "conn-1", "http://127.0.0.1:9", api_key=FAKE_KEY),
            binding("judge_two", "conn-2", "http://127.0.0.1:9", api_key="sk-fake-second-key-8642"),
        ),
        run_id="dry-conn", resume=False,
    )
    result = run_study("dry-conn", tmp_path, config)
    assert result["status"] == "completed"
    metrics = json.loads((tmp_path / "dry-conn" / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["pipeline_test_only"] is True
    assert set(metrics["judges"]) == {"judge_one", "judge_two"}
    for key in (FAKE_KEY, "sk-fake-second-key-8642"):
        _assert_key_nowhere(tmp_path / "dry-conn", key, capsys)


# --------------------------------------------------------------------------- #
# N-judge runner
# --------------------------------------------------------------------------- #
def test_three_connection_judges_run_against_the_fake_server(patched_loader, tmp_path: Path) -> None:
    with FakeOpenAIServer(mode="verdicts", expected_key=FAKE_KEY) as server:
        config = StudyConfig(
            dataset="synthetic", n_items=3, dry_run=False,
            connection_judges=(
                binding("judge_a", "conn-1", server.base_url, family_label="Family A"),
                binding("judge_b", "conn-2", server.base_url, family_label="Family B"),
                binding("judge_c", "conn-3", server.base_url, family_label="Family C"),
            ),
            max_api_calls=100, run_id="three-judges", resume=False,
        )
        result = run_study("three-judges", tmp_path, config)
    assert result["status"] == "completed"
    metrics = json.loads((tmp_path / "three-judges" / "metrics.json").read_text(encoding="utf-8"))
    assert set(metrics["judges"]) == {"judge_a", "judge_b", "judge_c"}
    for pair in ("judge_a|judge_b", "judge_a|judge_c", "judge_b|judge_c"):
        assert pair in metrics["cross_judge"]
    # The key reached the fake server as a bearer token, and the prompt never did.
    assert all(row["auth_matches_expected"] is True for row in server.requests if row["method"] == "POST")


def test_judge_prompts_carry_no_key(patched_loader, tmp_path: Path) -> None:
    with FakeOpenAIServer(mode="verdicts", expected_key=FAKE_KEY) as server:
        config = StudyConfig(
            dataset="synthetic", n_items=1, dry_run=False,
            connection_judges=(binding("judge_x", "conn-1", server.base_url),),
            run_id="prompt-check", resume=False,
        )
        run_study("prompt-check", tmp_path, config)
    prompt_rows = [row for row in server.requests if row["method"] == "POST"]
    assert prompt_rows
    assert all(row["model"] == "fake-model-judge_x" for row in prompt_rows)


# --------------------------------------------------------------------------- #
# Consistency subsample and caps
# --------------------------------------------------------------------------- #
def test_consistency_subsample_limits_repeats(patched_loader, tmp_path: Path) -> None:
    with FakeOpenAIServer(mode="verdicts", expected_key=FAKE_KEY) as server:
        config = StudyConfig(
            dataset="synthetic", n_items=4, repeats=1, dry_run=False,
            consistency_subsample=2,
            connection_judges=(binding("judge_s", "conn-1", server.base_url),),
            max_api_calls=100, run_id="subsample", resume=False,
        )
        result = run_study("subsample", tmp_path, config)
    assert result["status"] == "completed"
    rows = [
        json.loads(line)
        for line in (tmp_path / "subsample" / "judgements.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    by_repeat: dict[int, int] = {}
    for row in rows:
        by_repeat[row["repeat"]] = by_repeat.get(row["repeat"], 0) + 1
    assert by_repeat[0] == 4, "the primary pass covers every item once"
    assert by_repeat[1] == 2 and by_repeat[2] == 2, "the consistency pass covers the subsample only"
    items_with_three = {row["item_id"] for row in rows if row["repeat"] == 2}
    assert len(items_with_three) == 2
    metrics = json.loads((tmp_path / "subsample" / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["judges"]["judge_s"]["across_repeats"]["n_items_all_repeats"] == 2


def test_per_connection_call_cap_stops_the_run(patched_loader, tmp_path: Path, capsys) -> None:
    with FakeOpenAIServer(mode="verdicts", expected_key=FAKE_KEY) as server:
        config = StudyConfig(
            dataset="synthetic", n_items=6, repeats=1, dry_run=False,
            connection_judges=(binding("judge_cap", "conn-1", server.base_url, call_cap=3),),
            max_api_calls=100, run_id="capped", resume=False,
        )
        result = run_study("capped", tmp_path, config)
    assert result["status"] == "budget_stopped"
    rows = [
        json.loads(line)
        for line in (tmp_path / "capped" / "judgements.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    refusals = [row for row in rows if row["failure_type"] == "refused"]
    assert len(refusals) == 1, "the call the guard stopped is recorded as a refused judgement"
    scored = [row for row in rows if row["failure_type"] == "none"]
    assert len(scored) == 3, "the connection cap stopped the run after 3 calls"
    _assert_key_nowhere(tmp_path / "capped", FAKE_KEY, capsys)


# --------------------------------------------------------------------------- #
# Reasoning budget warning
# --------------------------------------------------------------------------- #
def test_truncation_produces_the_reasoning_warning(patched_loader, tmp_path: Path) -> None:
    with FakeOpenAIServer(mode="truncated", expected_key=FAKE_KEY, truncate_tokens=64) as server:
        config = StudyConfig(
            dataset="synthetic", n_items=2, repeats=1, dry_run=False,
            connection_judges=(binding("judge_t", "conn-1", server.base_url, max_tokens=64),),
            max_api_calls=100, run_id="truncated", resume=False,
        )
        result = run_study("truncated", tmp_path, config)
    assert result["status"] == "completed"
    warnings = result["usage"]["reasoning_budget_warnings"]
    assert warnings and warnings[0]["judge_id"] == "judge_t"
    assert warnings[0]["n_truncated_calls"] >= 1
    metrics = json.loads((tmp_path / "truncated" / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["judges"]["judge_t"]["primary_repeat"]["failure_counts"]["empty_reply"] >= 1


# --------------------------------------------------------------------------- #
# Backward compatibility
# --------------------------------------------------------------------------- #
def test_rebuild_still_reads_a_v02_style_run(patched_loader, tmp_path: Path) -> None:
    """v0.2 run directories have no failure_type; rebuild must classify from error."""
    with FakeOpenAIServer(mode="verdicts", expected_key=FAKE_KEY) as server:
        config = StudyConfig(
            dataset="synthetic", n_items=2, repeats=1, dry_run=False,
            connection_judges=(binding("judge_old", "conn-1", server.base_url),),
            run_id="old-style", resume=False,
        )
        run_study("old-style", tmp_path, config)
    path = tmp_path / "old-style" / "judgements.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row.pop("failure_type", None)  # simulate a v0.2 record
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    rebuilt = rebuild_run("old-style", run_root=tmp_path)
    primary = rebuilt["metrics"]["judges"]["judge_old"]["primary_repeat"]
    assert primary["failure_counts"]["none"] == len(rows)
