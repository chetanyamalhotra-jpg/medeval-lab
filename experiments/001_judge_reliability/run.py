"""Experiment 001 -- judge reliability under planted error.

Runs the pre-registered protocol in PLAN.md and writes verdicts, a summary
JSON, figures and a Markdown report under results/experiment_001/.

Executed with the deterministic rule judge plus simulated (MOCK) LLM judges,
because no API key, local model server or CPU model is available. Every mock
result is therefore labelled PIPELINE TEST ONLY.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(ROOT / "packages"))

from medeval_core.backends import BACKEND_MOCK, BackendSelection, select_backend  # noqa: E402
from medeval_core.calllog import JsonlCallSink, new_run_id  # noqa: E402
from medeval_core.client import LLMClient, MockTransport  # noqa: E402
from medeval_core.config import Settings, load_settings  # noqa: E402
from medeval_judges import (  # noqa: E402
    JuryJudge,
    LLMRubricJudge,
    MockPolicy,
    PairwiseCase,
    PairwiseVerdict,
    RuleJudge,
)
from medeval_judges.llm import _TextQueue  # noqa: E402
from medeval_reliability import (  # noqa: E402
    bootstrap_ci,
    cohen_kappa,
    gwet_ac1,
    mcnemar_test,
    percent_agreement,
    position_bias,
    proportion_ci,
    repeat_agreement,
)

EXPERIMENT_DIR = Path(__file__).resolve().parent
CASES_PATH = EXPERIMENT_DIR / "data" / "case_specs_v0.1.json"
ITEMS_PATH = ROOT / "data" / "items" / "seed_v0.1.json"
OUT_DIR = ROOT / "results" / "experiment_001"
FIG_DIR = OUT_DIR / "figures"

SEEDS = (20260101, 20260102, 20260103)
PRIMARY_SEED = SEEDS[0]
N_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20260101

JUDGE_IDS = ("rule_based", "llm_free_mock", "llm_guided_mock", "jury_mock", "llm_biased_mock")


@dataclass
class Case:
    case: PairwiseCase
    error_type: str

    @property
    def target_side(self) -> str:
        return self.case.target or "B"


def _bad_response_first(case_id: str) -> bool:
    """Deterministic, answer-independent assignment of the bad response.

    Half of the cases put the planted error in slot A. Without this, a
    position-biased judge would be indistinguishable from an accurate one.
    """
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2 == 0


def load_cases() -> list[Case]:
    items = {item["id"]: item for item in json.loads(ITEMS_PATH.read_text(encoding="utf-8"))}
    specs = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases: list[Case] = []
    for spec in specs:
        bad_first = _bad_response_first(spec["case_id"])
        response_a = spec["bad_response"] if bad_first else spec["good_response"]
        response_b = spec["good_response"] if bad_first else spec["bad_response"]
        item = items.get(spec["item_id"], {})
        case = PairwiseCase(
            case_id=spec["case_id"],
            item_id=spec["item_id"],
            question=item.get("text", ""),
            response_a=response_a,
            response_b=response_b,
            gold_answer=item.get("gold_answer"),
            required_elements=tuple(spec.get("required_elements", ())),
            target="A" if bad_first else "B",
            error_type=spec["error_type"],
            metadata={"planted_error": spec.get("error_description", "")},
        )
        cases.append(Case(case=case, error_type=spec["error_type"]))
    return cases


def build_judges(settings: Settings, selection: BackendSelection, run_id: str) -> dict[str, Any]:
    """Construct the five pre-registered judge configurations."""
    calls_path = OUT_DIR / "calls.jsonl"
    sink = JsonlCallSink(calls_path)

    def mock_judge(judge_id: str, policy: MockPolicy, reference_mode: str) -> LLMRubricJudge:
        queue = _TextQueue()
        client = LLMClient(
            MockTransport(responder=queue, model=f"mock-{judge_id}"),
            settings,
            sink=sink,
            run_id=run_id,
        )
        return LLMRubricJudge(
            client, judge_id=judge_id, reference_mode=reference_mode, mock_policy=policy, queue=queue
        )

    free = mock_judge("llm_free_mock", MockPolicy(accuracy=0.75, label="acc0.75"), "free")
    guided = mock_judge("llm_guided_mock", MockPolicy(accuracy=0.80, label="acc0.80"), "guided")
    biased = mock_judge(
        "llm_biased_mock", MockPolicy(accuracy=0.50, position_bias=0.80, label="pos0.80"), "free"
    )
    if selection.is_mock:
        judges: dict[str, Any] = {
            "rule_based": RuleJudge(),
            "llm_free_mock": free,
            "llm_guided_mock": guided,
            "jury_mock": JuryJudge(members=[free, guided], judge_id="jury_mock"),
            "llm_biased_mock": biased,
        }
    else:  # a real backend would replace the simulated judges, not add to them
        judges = {"rule_based": RuleJudge()}
    return judges


def run_primary(judges: dict[str, Any], cases: list[Case], seed: int) -> list[PairwiseVerdict]:
    verdicts: list[PairwiseVerdict] = []
    for judge in judges.values():
        for case in cases:
            for swapped in (False, True):
                verdicts.append(judge.compare(case.case, seed=seed, swapped=swapped))
    return verdicts


def run_self_consistency(judges: dict[str, Any], cases: list[Case]) -> list[PairwiseVerdict]:
    verdicts: list[PairwiseVerdict] = []
    for judge in judges.values():
        for seed in SEEDS:
            for case in cases:
                verdicts.append(judge.compare(case.case, seed=seed, swapped=False))
    return verdicts


def _side_label(verdict: PairwiseVerdict, case: Case) -> str:
    """Map a verdict to the judged identity of the response, independent of slot."""
    if verdict.winner == "tie":
        return "tie"
    return "bad" if verdict.winner == case.target_side else "good"


def _correct(verdict: PairwiseVerdict, case: Case) -> int | None:
    if verdict.winner == "tie" and verdict.error:
        return None
    return 1 if verdict.winner == case.target_side else 0


def analyse_primary(
    judges: dict[str, Any], cases: list[Case], verdicts: list[PairwiseVerdict]
) -> dict[str, Any]:
    by_case = {case.case.case_id: case for case in cases}
    summary: dict[str, Any] = {"judges": {}, "agreement": {}, "position": {}, "length": {}}

    canonical: dict[str, dict[str, PairwiseVerdict]] = {}

    for judge_id in judges:
        mapping: dict[str, PairwiseVerdict] = {}
        for v in verdicts:
            if v.judge_id != judge_id:
                continue
            # canonical = the verdict whose presented_first equals "A"
            if v.presented_first == "A" and v.case_id not in mapping:
                mapping[v.case_id] = v
        canonical[judge_id] = mapping

    for judge_id, verdicts_for_judge in canonical.items():
        correctness = [
            value
            for case_id, verdict in verdicts_for_judge.items()
            if (value := _correct(verdict, by_case[case_id])) is not None
        ]
        ci = bootstrap_ci(
            correctness, method="bca", seed=BOOTSTRAP_SEED, n_resamples=N_RESAMPLES
        ) if correctness else None
        per_error_type: dict[str, dict[str, float]] = {}
        for case_id, verdict in verdicts_for_judge.items():
            case = by_case[case_id]
            value = _correct(verdict, case)
            if value is None:
                continue
            bucket = per_error_type.setdefault(case.error_type, {"n": 0.0, "correct": 0.0})
            bucket["n"] += 1
            bucket["correct"] += value
        for bucket in per_error_type.values():
            bucket["accuracy"] = bucket["correct"] / bucket["n"] if bucket["n"] else float("nan")
        summary["judges"][judge_id] = {
            "n": len(verdicts_for_judge),
            "n_scored": len(correctness),
            "accuracy": (sum(correctness) / len(correctness)) if correctness else float("nan"),
            "ci": ci.as_dict() if ci else None,
            "n_errors": sum(1 for v in verdicts_for_judge.values() if v.error),
            "per_error_type": per_error_type,
            "backend": next(iter(verdicts_for_judge.values())).backend if verdicts_for_judge else "",
            "model": next(iter(verdicts_for_judge.values())).model if verdicts_for_judge else "",
            "pipeline_test_only": all(v.pipeline_test_only for v in verdicts_for_judge.values())
            if verdicts_for_judge
            else False,
        }

    # agreement on judged identity of the response
    labels = {
        judge_id: {case_id: _side_label(v, by_case[case_id]) for case_id, v in mapping.items()}
        for judge_id, mapping in canonical.items()
    }
    for first in judges:
        for second in judges:
            if first >= second:
                continue
            shared = sorted(set(labels[first]) & set(labels[second]))
            a = [labels[first][case_id] for case_id in shared]
            b = [labels[second][case_id] for case_id in shared]
            summary["agreement"][f"{first}|{second}"] = {
                "n": len(shared),
                "percent_agreement": percent_agreement(a, b),
                "cohen_kappa": cohen_kappa(a, b),
                "gwet_ac1": gwet_ac1(a, b),
            }

    # position bias over both presentation orders
    for judge_id in judges:
        records = [
            {
                "case_id": v.case_id,
                "winner": v.winner,
                "presented_first": v.presented_first,
                "winner_slot": 1 if v.winner == v.presented_first else (2 if v.winner != "tie" else None),
                "swapped": v.presented_first == "B",
            }
            for v in verdicts
            if v.judge_id == judge_id and v.winner != "tie"
        ]
        try:
            result = position_bias(records)
            summary["position"][judge_id] = result.as_dict()
        except ValueError as exc:
            summary["position"][judge_id] = {"error": str(exc)}

    # length association on canonical verdicts
    for judge_id, mapping in canonical.items():
        diffs: list[float] = []
        for case_id, verdict in mapping.items():
            if verdict.winner == "tie":
                continue
            case = by_case[case_id].case
            chosen = case.response(verdict.winner)
            other = case.response("B" if verdict.winner == "A" else "A")
            diffs.append(float(len(chosen) - len(other)))
        summary["length"][judge_id] = (
            bootstrap_ci(diffs, method="bca", seed=BOOTSTRAP_SEED, n_resamples=2000).as_dict()
            if diffs
            else {"error": "no non-tie verdicts"}
        )
    return summary


def analyse_self_consistency(
    cases: list[Case], verdicts: list[PairwiseVerdict]
) -> dict[str, Any]:
    by_case = {case.case.case_id: case for case in cases}
    out: dict[str, Any] = {}
    for judge_id in JUDGE_IDS:
        ratings: list[list[str]] = []
        for case_id in sorted(by_case):
            row = [
                _side_label(v, by_case[case_id])
                for v in verdicts
                if v.judge_id == judge_id and v.case_id == case_id
            ]
            if row:
                ratings.append(row)
        stats = repeat_agreement(ratings)
        out[judge_id] = {key: value for key, value in stats.items()}
    return out


def analyse_paired(summary: dict[str, Any], cases: list[Case], verdicts: list[PairwiseVerdict]) -> dict[str, Any]:
    by_case = {case.case.case_id: case for case in cases}
    per_judge: dict[str, dict[str, int]] = {}
    for judge_id in JUDGE_IDS:
        mapping: dict[str, int] = {}
        for v in verdicts:
            if v.judge_id != judge_id or v.presented_first != "A":
                continue
            value = _correct(v, by_case[v.case_id])
            if value is not None and v.case_id not in mapping:
                mapping[v.case_id] = value
        per_judge[judge_id] = mapping
    out: dict[str, Any] = {}
    ids = list(per_judge)
    for i, first in enumerate(ids):
        for second in ids[i + 1 :]:
            shared = sorted(set(per_judge[first]) & set(per_judge[second]))
            if not shared:
                continue
            a = [bool(per_judge[first][case_id]) for case_id in shared]
            b = [bool(per_judge[second][case_id]) for case_id in shared]
            out[f"{first}|{second}"] = mcnemar_test(a, b)
    return out


def plot(summary: dict[str, Any], consistency: dict[str, Any], out_dir: Path) -> list[Path]:
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".tmp" / "mpl"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    judges = [judge_id for judge_id in JUDGE_IDS if judge_id in summary["judges"]]

    # 1. accuracy forest plot
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ys = list(range(len(judges)))
    for y, judge_id in zip(ys, judges, strict=False):
        entry = summary["judges"][judge_id]
        ci = entry.get("ci") or {}
        acc = entry["accuracy"]
        low = ci.get("low", acc)
        high = ci.get("high", acc)
        ax.plot([low, high], [y, y], color="#3b6ea5", lw=2)
        ax.plot(acc, y, "o", color="#1f3d5c", ms=6)
        ax.text(high + 0.02, y, f"{acc:.2f} [{low:.2f}, {high:.2f}]", va="center", fontsize=8)
    ax.set_yticks(ys)
    ax.set_yticklabels(judges, fontsize=9)
    ax.axvline(0.5, color="grey", ls="--", lw=1)
    ax.set_xlim(0, 1.32)
    ax.set_xlabel("Accuracy against planted ground truth (95% BCa CI)")
    ax.set_title("Experiment 001: judge accuracy\n(dashed line = chance; MOCK judges are pipeline tests only)", fontsize=9)
    fig.tight_layout()
    path = out_dir / "accuracy_with_ci.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    written.append(path)

    # 2. agreement heatmap
    matrix = []
    for first in judges:
        row = []
        for second in judges:
            if first == second:
                row.append(1.0)
            else:
                key = f"{first}|{second}" if first < second else f"{second}|{first}"
                row.append(summary["agreement"].get(key, {}).get("cohen_kappa", float("nan")))
        matrix.append(row)
    fig, ax = plt.subplots(figsize=(5.6, 4.8))
    image = ax.imshow(matrix, vmin=-0.2, vmax=1.0, cmap="RdBu_r")
    ax.set_xticks(range(len(judges)))
    ax.set_xticklabels(judges, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(judges)))
    ax.set_yticklabels(judges, fontsize=8)
    for i in range(len(judges)):
        for j in range(len(judges)):
            value = matrix[i][j]
            ax.text(j, i, "n/a" if value != value else f"{value:.2f}", ha="center", va="center", fontsize=8)
    fig.colorbar(image, ax=ax, shrink=0.8, label="Cohen's kappa")
    ax.set_title("Judge-judge agreement (agreement on which response is worse)", fontsize=9)
    fig.tight_layout()
    path = out_dir / "agreement_matrix.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    written.append(path)

    # 3. self-consistency
    fig, ax = plt.subplots(figsize=(7, 3.2))
    values = [consistency.get(judge_id, {}).get("exact_agreement", float("nan")) for judge_id in judges]
    ax.barh(list(range(len(judges))), values, color="#4b8b6b")
    ax.set_yticks(list(range(len(judges))))
    ax.set_yticklabels(judges, fontsize=9)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("Exact agreement across 3 seeds")
    ax.set_title("Self-consistency (deterministic judges should be 1.0)", fontsize=9)
    fig.tight_layout()
    path = out_dir / "self_consistency.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    written.append(path)

    # 4. position bias
    fig, ax = plt.subplots(figsize=(7, 3.2))
    rates = []
    lows = []
    highs = []
    for judge_id in judges:
        entry = summary["position"].get(judge_id, {})
        rate = entry.get("first_slot_rate", float("nan"))
        n = int(entry.get("n_pairs", 0) or 0)
        successes = round(rate * n) if n and rate == rate else 0
        low, high = proportion_ci(successes, n) if n else (float("nan"), float("nan"))
        rates.append(rate)
        lows.append(low)
        highs.append(high)
    ax.barh(list(range(len(judges))), rates, color="#a5682a")
    for y, (low, high) in enumerate(zip(lows, highs, strict=False)):
        ax.plot([low, high], [y, y], color="black", lw=1)
    ax.axvline(0.5, color="grey", ls="--", lw=1)
    ax.set_yticks(list(range(len(judges))))
    ax.set_yticklabels(judges, fontsize=9)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("Share of verdicts naming the response shown first")
    ax.set_title("Position bias (dashed line = no preference; mock_biased injected 0.8)", fontsize=9)
    fig.tight_layout()
    path = out_dir / "position_bias.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    written.append(path)
    return written


def write_report(summary: dict[str, Any], consistency: dict[str, Any], paired: dict[str, Any], judges: dict[str, Any], selection: BackendSelection, figures: list[Path]) -> str:
    lines: list[str] = []
    lines.append("# Experiment 001 — results\n")
    lines.append(f"Backend for LLM-family judges: **{selection.label}**\n")
    lines.append(
        "> MOCK judges are simulations with a configured accuracy; their numbers "
        "validate the analysis pipeline and must not be read as properties of any "
        "real model. PIPELINE TEST ONLY.\n"
    )
    lines.append("## Primary result: accuracy against planted ground truth\n")
    lines.append("| judge | backend | n | accuracy | 95% BCa CI | n errors |")
    lines.append("|---|---|---|---|---|---|")
    for judge_id, entry in summary["judges"].items():
        ci = entry.get("ci") or {}
        lines.append(
            f"| {judge_id} | {entry['backend']}:{entry['model']} | {entry['n_scored']} | "
            f"{entry['accuracy']:.3f} | [{ci.get('low', float('nan')):.3f}, {ci.get('high', float('nan')):.3f}] | "
            f"{entry['n_errors']} |"
        )
    lines.append("")
    lines.append("## Agreement between judges\n")
    lines.append("| pair | n | percent agreement | Cohen's kappa | Gwet's AC1 |")
    lines.append("|---|---|---|---|---|")
    for pair, entry in summary["agreement"].items():
        kappa = entry["cohen_kappa"]
        ac1 = entry.get("gwet_ac1", float("nan"))
        lines.append(
            f"| {pair} | {entry['n']} | {entry['percent_agreement']:.3f} | "
            f"{'undefined' if kappa != kappa else f'{kappa:.3f}'} | "
            f"{'undefined' if ac1 != ac1 else f'{ac1:.3f}'} |"
        )
    lines.append("")
    lines.append("## Self-consistency across seeds\n")
    lines.append("| judge | cases | repeats | exact agreement | unanimous rate | Krippendorff alpha |")
    lines.append("|---|---|---|---|---|---|")
    for judge_id, entry in consistency.items():
        alpha = entry.get("alpha", float("nan"))
        lines.append(
            f"| {judge_id} | {int(entry.get('n_cases', 0))} | {int(entry.get('n_repeats', 0))} | "
            f"{entry.get('exact_agreement', float('nan')):.3f} | {entry.get('unanimous_rate', float('nan')):.3f} | "
            f"{'undefined' if alpha != alpha else f'{alpha:.3f}'} |"
        )
    lines.append("")
    lines.append("## Position bias\n")
    lines.append("| judge | n verdicts | first-slot rate | exact binomial p | swap consistency | n swap pairs |")
    lines.append("|---|---|---|---|---|---|")
    for judge_id, entry in summary["position"].items():
        if "error" in entry:
            lines.append(f"| {judge_id} | - | - | - | - | {entry['error']} |")
            continue
        consistency_value = entry.get("swap_consistency")
        lines.append(
            f"| {judge_id} | {entry['n_pairs']} | {entry['first_slot_rate']:.3f} | {entry['exact_binomial_p']:.3g} | "
            f"{'n/a' if consistency_value is None else f'{consistency_value:.3f}'} | {entry['n_swap_pairs']} |"
        )
    lines.append("")
    lines.append("## Length association\n")
    lines.append("| judge | mean length difference (chosen worse minus other) | 95% CI |")
    lines.append("|---|---|---|")
    for judge_id, entry in summary["length"].items():
        if "error" in entry:
            lines.append(f"| {judge_id} | - | {entry['error']} |")
            continue
        lines.append(
            f"| {judge_id} | {entry['estimate']:.1f} chars | [{entry['low']:.1f}, {entry['high']:.1f}] |"
        )
    lines.append("")
    lines.append("## Subgroup accuracy by planted error type\n")
    error_types = sorted({et for entry in summary["judges"].values() for et in entry["per_error_type"]})
    lines.append("| judge | " + " | ".join(error_types) + " |")
    lines.append("|---" * (len(error_types) + 1) + "|")
    for judge_id, entry in summary["judges"].items():
        cells = []
        for error_type in error_types:
            bucket = entry["per_error_type"].get(error_type)
            cells.append("n/a" if not bucket else f"{bucket['accuracy']:.2f} (n={int(bucket['n'])})")
        lines.append(f"| {judge_id} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Paired comparisons (McNemar, canonical order)\n")
    lines.append("| pair | b01 | b10 | p | method |")
    lines.append("|---|---|---|---|---|")
    for pair, entry in paired.items():
        lines.append(
            f"| {pair} | {int(entry['b01'])} | {int(entry['b10'])} | {entry['p_value']:.3g} | {entry['method']} |"
        )
    lines.append("")
    lines.append("## Figures\n")
    for path in figures:
        lines.append(f"- `{path.relative_to(ROOT).as_posix()}`")
    lines.append("")
    lines.append("## What these results can and cannot support\n")
    lines.append(
        "Supported: the rule judge's coverage of the five planted error types; the "
        "precision of the reliability statistics at n=50; that injected position "
        "bias is recovered by the bias probe; that the analysis code recovers the "
        "simulated judges' configured accuracy within interval error.\n"
    )
    lines.append(
        "Not supported: any statement about a real LLM judge; any clinical claim; "
        "any generalisation beyond these 50 synthetic cases. The rule judge was "
        "written with these cases in view, so its accuracy is an upper bound.\n"
    )
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Experiment 001 is permanently a pipeline test. It must never inherit a
    # configured real endpoint from the shell or `.env`.
    configured = load_settings()
    settings = replace(configured, base_url=None, model=None, api_key=None, backend_override="mock", hf_model=None)
    selection = select_backend(settings)
    if selection.name != BACKEND_MOCK:
        raise RuntimeError("Experiment 001 safety failure: mock backend could not be forced")
    run_id = new_run_id("exp001")
    cases = load_cases()
    judges = build_judges(settings, selection, run_id)

    primary = run_primary(judges, cases, PRIMARY_SEED)
    consistency_verdicts = run_self_consistency(judges, cases)

    summary = analyse_primary(judges, cases, primary)
    consistency = analyse_self_consistency(cases, consistency_verdicts)
    paired = analyse_paired(summary, cases, primary)
    figures = plot(summary, consistency, FIG_DIR)

    payload = {
        "experiment": "001_judge_reliability",
        "run_id": run_id,
        "backend": selection.as_dict(),
        "settings": settings.safe_dict(),
        "seeds": list(SEEDS),
        "primary_seed": PRIMARY_SEED,
        "n_cases": len(cases),
        "judges": {judge_id: getattr(judge, "backend", "") for judge_id, judge in judges.items()},
        "summary": summary,
        "self_consistency": consistency,
        "paired": paired,
        "figures": [str(path.relative_to(ROOT)) for path in figures],
        "pipeline_test_only": selection.is_mock,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with (OUT_DIR / "verdicts.jsonl").open("w", encoding="utf-8") as handle:
        for verdict in primary + consistency_verdicts:
            handle.write(json.dumps(verdict.as_dict(), ensure_ascii=False) + "\n")
    report = write_report(summary, consistency, paired, judges, selection, figures)
    (OUT_DIR / "report.md").write_text(report, encoding="utf-8")
    print(f"backend: {selection.label}")
    print(f"wrote {OUT_DIR / 'summary.json'}")
    print(f"wrote {OUT_DIR / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
