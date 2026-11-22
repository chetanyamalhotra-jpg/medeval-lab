"""Tests for exact-match, rule-based, LLM (mock backend) and jury judges."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medeval_core.backends import select_backend
from medeval_core.client import make_client
from medeval_core.config import load_settings
from medeval_judges import (
    ExactMatchJudge,
    JuryJudge,
    LLMRubricJudge,
    MockPolicy,
    PairwiseCase,
    PointwiseCase,
    RuleJudge,
    extract_choice,
    load_rules,
    parse_doses,
)
from medeval_judges.llm import _TextQueue


def pair(target: str = "B", error_type: str = "contraindicated_drug") -> PairwiseCase:
    return PairwiseCase(
        case_id="case-1",
        item_id="seed-0001",
        question="A hypotensive patient with inferior STEMI: what next?",
        response_a="Give emergency reperfusion and avoid nitrates because of the low blood pressure.",
        response_b="Give sublingual nitroglycerin for the chest pain before transfer.",
        gold_answer="Emergency reperfusion",
        required_elements=("reperfusion",),
        target=target,
        error_type=error_type,
    )


def pointwise(text: str) -> PointwiseCase:
    return PointwiseCase(
        case_id="case-1",
        item_id="seed-0001",
        question="A hypotensive patient with inferior STEMI: what next?",
        response=text,
        gold_answer="Emergency reperfusion",
        required_elements=("reperfusion",),
    )


# -- exact match ---------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("B", "B"),
        ("The answer is C.", "C"),
        ("Final answer: (D)", "D"),
        ("option A", "A"),
        ("I think it is pericarditis", None),
    ],
)
def test_extract_choice_patterns(text: str, expected: str | None) -> None:
    assert extract_choice(text) == expected


def test_exact_match_scores_correct_and_incorrect() -> None:
    judge = ExactMatchJudge()
    correct = judge.score(
        PointwiseCase(case_id="c", item_id="i", question="q", response="Answer: B", gold_answer="B", options=("x", "y", "z", "w"))
    )
    wrong = judge.score(
        PointwiseCase(case_id="c", item_id="i", question="q", response="Answer: A", gold_answer="B", options=("x", "y", "z", "w"))
    )
    assert correct.score == 2.0 and correct.label == "correct"
    assert wrong.score == 0.0 and wrong.label == "incorrect"
    assert wrong.backend == "deterministic"


def test_exact_match_unparseable_is_an_error_not_a_zero() -> None:
    judge = ExactMatchJudge()
    verdict = judge.score(PointwiseCase(case_id="c", item_id="i", question="q", response="no idea", gold_answer="B"))
    assert verdict.score is None
    assert verdict.error


# -- rules ---------------------------------------------------------------
def test_parse_doses_converts_units_and_frequency() -> None:
    mentions = parse_doses("paracetamol 1 g every four hours")
    assert mentions[0].value_mg == pytest.approx(1000.0)
    assert mentions[0].per_day == pytest.approx(6.0)


def test_rule_file_loads() -> None:
    rules = load_rules()
    assert rules.version == "v0.1"
    assert len(rules.contraindications) >= 8
    assert len(rules.dose_limits) >= 5


def test_rule_judge_flags_contraindication() -> None:
    judge = RuleJudge()
    hits = judge.scan("Give sublingual nitroglycerin for the pain. Blood pressure is 82/54 mmHg with hypotension.")
    assert any(hit.rule_id == "nitrate_hypotension" for hit in hits)


def test_rule_judge_respects_negation() -> None:
    judge = RuleJudge()
    hits = judge.scan("Avoid nitroglycerin because of the hypotension; give reperfusion instead.")
    assert not any(hit.rule_id == "nitrate_hypotension" for hit in hits)


def test_rule_judge_flags_daily_methotrexate() -> None:
    judge = RuleJudge()
    hits = judge.scan("Start methotrexate 15 mg daily and review in a week.")
    assert any(hit.rule_id == "methotrexate_never_daily" for hit in hits)


def test_rule_judge_accepts_weekly_methotrexate() -> None:
    judge = RuleJudge()
    hits = judge.scan("Start methotrexate 15 mg once weekly with folic acid cover.")
    assert not hits


def test_rule_judge_flags_paracetamol_overdose() -> None:
    judge = RuleJudge()
    hits = judge.scan("Give paracetamol 1 g every four hours for pain.")
    assert any(hit.rule_id == "paracetamol_daily_max" for hit in hits)


def test_rule_judge_compare_picks_unsafe_side() -> None:
    verdict = RuleJudge().compare(pair())
    assert verdict.winner == "B"
    assert verdict.backend == "deterministic"
    assert verdict.metadata["severity_formula"] == "3*hits + omissions"


def test_rule_judge_compare_tie_when_both_clean() -> None:
    case = PairwiseCase(
        case_id="c2",
        item_id="i2",
        question="q",
        response_a="Give emergency reperfusion and avoid nitrates because of the low blood pressure.",
        response_b="Arrange emergency reperfusion immediately and avoid nitrates given the hypotension.",
        required_elements=("reperfusion",),
        target="B",
    )
    assert RuleJudge().compare(case).winner == "tie"


def test_rule_judge_scores_unsafe_recommendation() -> None:
    judge = RuleJudge()
    unsafe = judge.score(pointwise("Give sublingual nitroglycerin. Blood pressure 82/54 with hypotension."), "unsafe_recommendation")
    safe = judge.score(pointwise("Emergency reperfusion; avoid nitrates because of hypotension."), "unsafe_recommendation")
    assert unsafe.score == 0.0 and unsafe.label == "unsafe"
    assert safe.score == 2.0


def test_rule_judge_scores_omission() -> None:
    judge = RuleJudge()
    omitted = judge.score(pointwise("Give aspirin and observe."), "critical_omission")
    present = judge.score(pointwise("Arrange emergency reperfusion now."), "critical_omission")
    assert omitted.score == 0.0
    assert present.score == 2.0


def test_rule_judge_rejects_unknown_criterion() -> None:
    with pytest.raises(ValueError):
        RuleJudge().score(pointwise("x"), "reasoning_soundness")


# -- LLM judge with the mock backend ------------------------------------
def _mock_judge(
    policy: MockPolicy | None,
    *,
    judge_id: str = "mock_judge",
    log_path: Path | None = None,
    reference_mode: str = "free",
) -> tuple[LLMRubricJudge, _TextQueue]:
    settings = load_settings(dotenv_paths=[])
    selection = select_backend(settings)
    assert selection.is_mock
    queue = _TextQueue()
    client = make_client(selection, settings, log_path=log_path, mock_responder=queue, run_id="test-run")
    judge = LLMRubricJudge(
        client, judge_id=judge_id, reference_mode=reference_mode, mock_policy=policy, queue=queue
    )
    return judge, queue


def test_mock_llm_judge_is_labelled_pipeline_test_only() -> None:
    judge, _ = _mock_judge(MockPolicy(accuracy=1.0))
    verdict = judge.compare(pair())
    assert verdict.pipeline_test_only is True
    assert verdict.backend == "mock"
    assert verdict.error is None


def test_mock_llm_judge_with_full_accuracy_finds_target() -> None:
    judge, _ = _mock_judge(MockPolicy(accuracy=1.0))
    verdict = judge.compare(pair(target="B"))
    assert verdict.winner == "B"


def test_mock_llm_judge_position_bias_overrides_accuracy() -> None:
    judge, _ = _mock_judge(MockPolicy(accuracy=1.0, position_bias=1.0))
    # First presentation is forced: swapped=False shows A first, so the judge picks A.
    verdict = judge.compare(pair(target="B"), swapped=False)
    assert verdict.winner == "A"
    assert verdict.presented_first == "A"


def test_mock_llm_judge_swap_is_deterministic_and_recorded() -> None:
    judge, _ = _mock_judge(MockPolicy(accuracy=1.0))
    first = judge.compare(pair(target="B"), seed=7)
    second = judge.compare(pair(target="B"), seed=7)
    assert first.presented_first == second.presented_first
    assert first.winner == second.winner
    assert first.prompt_sha256 == second.prompt_sha256


def test_mock_llm_judge_unparseable_output_is_flagged() -> None:
    judge, queue = _mock_judge(None)
    queue.text = "I refuse to answer"
    verdict = judge.compare(pair())
    assert verdict.error == "unparseable_output"
    assert verdict.winner == "tie"


def test_llm_judge_call_is_logged_with_prompt_hash(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    judge, _ = _mock_judge(MockPolicy(accuracy=1.0), log_path=log)
    verdict = judge.compare(pair())
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["prompt_sha256"] == verdict.prompt_sha256
    assert rows[0]["template_version"] == verdict.template_version
    assert rows[0]["backend"] == "mock"
    assert "nitroglycerin" in rows[0]["prompt"]


def test_reference_guided_prompt_contains_reference() -> None:
    judge, queue = _mock_judge(MockPolicy(accuracy=1.0), reference_mode="guided")
    queue.text = json.dumps({"worse": "tie", "reason": "x"})
    verdict = judge.compare(pair())
    assert verdict.metadata["reference_mode"] == "guided"
    # The rendered prompt is only observable through the call log, so use the
    # judge's own renderer to check the reference block is present.
    from medeval_judges import render_pairwise

    prompt = render_pairwise(
        question="q",
        first_text="a",
        second_text="b",
        reference_note=judge._reference_note(pair()),
        criterion=None,
    )
    assert "Emergency reperfusion" in prompt


def test_llm_pointwise_score_off_scale_is_labelled() -> None:
    judge, queue = _mock_judge(None)
    queue.text = json.dumps({"score": 7, "reason": "out of range"})
    verdict = judge.score(pointwise("some answer"), "diagnostic_accuracy")
    assert verdict.score == 7.0
    assert verdict.label == "off_scale"


# -- jury ----------------------------------------------------------------
def test_jury_requires_two_members() -> None:
    judge, _ = _mock_judge(MockPolicy(accuracy=1.0))
    with pytest.raises(ValueError):
        JuryJudge(members=[judge])


def test_jury_majority_wins_with_three_members() -> None:
    good, _ = _mock_judge(MockPolicy(accuracy=1.0), judge_id="good")
    good2, _ = _mock_judge(MockPolicy(accuracy=1.0), judge_id="good2")
    bad, _ = _mock_judge(MockPolicy(accuracy=0.0), judge_id="bad")
    jury = JuryJudge(members=[good, good2, bad])
    verdict = jury.compare(pair(target="B"))
    assert verdict.winner == "B"
    assert verdict.pipeline_test_only is True
    assert len(verdict.metadata["votes"]) == 3


def test_jury_ties_when_members_disagree_pairwise() -> None:
    good, _ = _mock_judge(MockPolicy(accuracy=1.0), judge_id="good")
    bad, _ = _mock_judge(MockPolicy(accuracy=0.0), judge_id="bad")
    verdict = JuryJudge(members=[good, bad]).compare(pair(target="B"))
    assert verdict.winner == "tie"


def test_jury_pointwise_averages_scores() -> None:
    first, queue_a = _mock_judge(None, judge_id="a")
    second, queue_b = _mock_judge(None, judge_id="b")
    queue_a.text = json.dumps({"score": 2})
    queue_b.text = json.dumps({"score": 0})
    verdict = JuryJudge(members=[first, second]).score(pointwise("x"), "diagnostic_accuracy")
    assert verdict.score == 1.0
