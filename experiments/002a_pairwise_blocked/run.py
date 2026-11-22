"""Experiment 002: guarded real-judge evaluation with a dry-run first."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))

from medeval_core.backends import select_backend
from medeval_core.calllog import JsonlCallSink, new_run_id, read_calls
from medeval_core.client import make_client
from medeval_core.config import load_settings
from medeval_core.provider import list_models
from medeval_core.safety import PriceCard, ResponseCache, SpendGuard
from medeval_judges import LLMRubricJudge, PairwiseCase, RuleJudge
from medeval_reliability import bootstrap_ci, cohen_kappa, gwet_ac1, percent_agreement

HERE = Path(__file__).resolve().parent
OUT = ROOT / "results" / "experiment_002"


def _target_first(case_id: str) -> bool:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2 == 0


def load_cases() -> list[PairwiseCase]:
    """Load 50 public pair specs plus 20 post-freeze held-out cases.

    This deliberately does not read ``data/items``: Experiment 002 must not
    access the removed/sealed hidden split. The pair responses themselves are
    sufficient for the blinded danger comparison; a generic question label is
    used for the historical v0.1 pairs.
    """
    original = json.loads((ROOT / "experiments" / "001_judge_reliability" / "data" / "case_specs_v0.1.json").read_text(encoding="utf-8"))
    overlay = json.loads((HERE / "data" / "case_set_v0.2.json").read_text(encoding="utf-8"))
    changes = {row["case_id"]: row for row in overlay["changes"]}
    heldout = json.loads((HERE / "data" / "heldout_v0.2.json").read_text(encoding="utf-8"))
    cases: list[PairwiseCase] = []
    for row in [*original, *heldout]:
        row = dict(row)
        if row["case_id"] in changes:
            row[changes[row["case_id"]]["field"]] = changes[row["case_id"]]["replacement"]
        first_bad = _target_first(row["case_id"])
        cases.append(PairwiseCase(case_id=row["case_id"], item_id=row["item_id"], question=row.get("question", f"Author-written pairwise safety case {row['case_id']}."), response_a=row["bad_response"] if first_bad else row["good_response"], response_b=row["good_response"] if first_bad else row["bad_response"], required_elements=tuple(row.get("required_elements", ())), target="A" if first_bad else "B", error_type=row["error_type"], metadata={"reference_standard": "planted_error", "set": "heldout_v0.2" if row["case_id"].startswith("heldout-") else "v0.1_overlay_v0.2"}))
    return cases


def planned_calls(n_cases: int) -> dict[str, int]:
    # 2 DeepSeek prompt configurations × two orders × three zero-temperature repeats.
    zero = 2 * n_cases * 2 * 3
    # One extra nonzero-temperature self-consistency call per configuration/case.
    stochastic = 2 * n_cases
    return {"smoke": 3, "temperature_zero": zero, "temperature_gt_zero": stochastic, "total_paid_max": 3 + zero + stochastic}


def _winner_identity(verdict: Any, case: PairwiseCase) -> str:
    if verdict.winner == "tie":
        return "tie"
    return "bad" if verdict.winner == case.target else "good"


def _summarise(verdicts: list[Any], cases: list[PairwiseCase]) -> dict[str, Any]:
    by_case = {case.case_id: case for case in cases}
    out: dict[str, Any] = {"judges": {}, "agreement": {}}
    by_judge: dict[str, list[Any]] = {}
    for verdict in verdicts:
        by_judge.setdefault(verdict.judge_id, []).append(verdict)
    for judge, rows in by_judge.items():
        primary = [v for v in rows if v.seed == 20260101 and v.presented_first == "A"]
        scored = [int(v.winner == by_case[v.case_id].target) for v in primary if not v.error and v.winner != "tie"]
        ci = bootstrap_ci(scored, method="bca", seed=20260101, n_resamples=2000).as_dict() if scored else None
        out["judges"][judge] = {"n_primary": len(primary), "n_scored": len(scored), "accuracy": sum(scored) / len(scored) if scored else None, "accuracy_ci": ci, "api_errors": sum(bool(v.error and v.error != "unparseable_output") for v in rows), "parse_failures": sum(v.error in {"unparseable_output", "invalid_winner"} for v in rows), "failure_examples": [v.as_dict() for v in primary if v.error or v.winner != by_case[v.case_id].target][:10]}
    names = sorted(by_judge)
    for i, first in enumerate(names):
        first_map = {v.case_id: _winner_identity(v, by_case[v.case_id]) for v in by_judge[first] if v.seed == 20260101 and v.presented_first == "A"}
        for second in names[i + 1:]:
            second_map = {v.case_id: _winner_identity(v, by_case[v.case_id]) for v in by_judge[second] if v.seed == 20260101 and v.presented_first == "A"}
            ids = sorted(set(first_map) & set(second_map))
            a, b = [first_map[x] for x in ids], [second_map[x] for x in ids]
            out["agreement"][f"{first}|{second}"] = {"n": len(ids), "raw_agreement": percent_agreement(a, b), "cohen_kappa": cohen_kappa(a, b), "gwet_ac1": gwet_ac1(a, b)}
    return out


def run_frozen_rule_only() -> int:
    """Score the post-freeze held-out cases without a model API call."""
    cases = [case for case in load_cases() if case.case_id.startswith("heldout-")]
    rule = RuleJudge()
    verdicts = [rule.compare(case, seed=20260101, swapped=False) for case in cases]
    correctness = [int(v.winner == case.target) for v, case in zip(verdicts, cases, strict=True)]
    payload = {
        "judge": "rule_frozen_v0.1",
        "reference_standard": "planted_error",
        "case_set": "heldout_v0.2",
        "n": len(cases),
        "accuracy": sum(correctness) / len(correctness),
        "accuracy_ci": bootstrap_ci(correctness, method="bca", seed=20260101, n_resamples=2000).as_dict(),
        "n_ties": sum(v.winner == "tie" for v in verdicts),
        "rule_file_sha256": "dbb62b978cbc2d1b9b65b59ae50e68cc5dc5ccdfba6c52a7689fa2a0758ab018",
        "freeze_commit": "6d0d8666b840bc5a92fa77bb426f213771bba050",
        "limitations": "Held-out cases intentionally extend beyond the narrow v0.1 rules; ties count as misses in this pairwise accuracy.",
        "verdicts": [v.as_dict() for v in verdicts],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rule_heldout_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k not in {"verdicts"}}, indent=2))
    return 0


def run(dry_run: bool = False) -> int:
    cases = load_cases()
    plan = planned_calls(len(cases))
    settings = load_settings()
    prices = PriceCard(settings.input_usd_per_million, settings.output_usd_per_million, settings.reasoning_usd_per_million, settings.price_source)
    estimate = prices.estimate(plan["total_paid_max"] * 900, plan["total_paid_max"] * settings.max_tokens)
    print(json.dumps({"dry_run": dry_run, "n_cases": len(cases), "calls": plan, "max_tokens": settings.max_tokens, "estimated_usd_at_max_tokens": estimate, "price_source": prices.source, "call_cap": settings.max_calls, "usd_cap": settings.max_usd}, indent=2))
    if dry_run:
        return 0
    selection = select_backend(settings)
    if selection.pipeline_test_only or not settings.has_api_credentials:
        print("Experiment 002 not run: a configured real API backend is required; no mock results are written.")
        return 2
    models = list_models(selection.base_url or "", settings.api_key, settings.timeout_s)
    if selection.model not in models:
        print("Experiment 002 not run: configured model is absent from the provider models response.")
        return 2
    guard = SpendGuard(settings.max_calls, settings.max_usd, prices)
    if plan["total_paid_max"] > settings.max_calls:
        print("Experiment 002 not run: dry-run plan exceeds call cap.")
        return 2
    if estimate is not None and estimate > settings.max_usd:
        print("Experiment 002 not run: dry-run estimate exceeds USD cap.")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = new_run_id("exp002")
    client = make_client(selection, settings, run_id=run_id, log_path=OUT / "calls.jsonl")
    client.guard = guard
    client.cache = ResponseCache(OUT / "response_cache.json")
    free = LLMRubricJudge(client, judge_id="deepseek_free", reference_mode="free")
    guided = LLMRubricJudge(client, judge_id="deepseek_guided", reference_mode="guided")
    # Smoke first: any API/parse failure stops the run and is retained in calls.jsonl.
    smoke = [free.compare(cases[0], seed=910001, swapped=False), guided.compare(cases[0], seed=910002, swapped=True), free.compare(cases[1], seed=910003, swapped=False)]
    if any(v.error for v in smoke):
        (OUT / "smoke_failures.json").write_text(json.dumps([v.as_dict() for v in smoke], indent=2), encoding="utf-8")
        print("Experiment 002 stopped: smoke stage had API or JSON-parse failure.")
        return 2
    verdicts: list[Any] = []
    rule = RuleJudge()
    for judge in (free, guided, rule):
        for seed in (20260101, 20260102, 20260103):
            for case in cases:
                for swapped in (False, True):
                    verdicts.append(judge.compare(case, seed=seed, swapped=swapped))
    # Separate temperature>0 configuration; restore setting immutably for the run.
    from dataclasses import replace
    client.settings = replace(settings, temperature=0.7)
    for judge in (free, guided):
        for case in cases:
            verdicts.append(judge.compare(case, seed=20260201, swapped=False))
    (OUT / "verdicts.jsonl").write_text("".join(json.dumps(v.as_dict(), ensure_ascii=False) + "\n" for v in verdicts), encoding="utf-8")
    report = _summarise(verdicts, cases)
    calls = read_calls(OUT / "calls.jsonl")
    report["cost_report"] = {**guard.summary(), "cache_hits": sum(bool(r.get("metadata", {}).get("cache_hit")) for r in calls), "input_tokens": sum(int(r.get("metadata", {}).get("input_tokens", 0)) for r in calls), "output_tokens": sum(int(r.get("metadata", {}).get("output_tokens", 0)) for r in calls), "reasoning_tokens": sum(int(r.get("metadata", {}).get("reasoning_tokens", 0)) for r in calls)}
    report["model_discovery"] = {"model": selection.model, "models_returned": models, "run_id": run_id}
    (OUT / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rule-only", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run_frozen_rule_only() if args.rule_only else run(args.dry_run))
