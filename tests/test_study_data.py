"""Sampling, candidate construction, prompt isolation and reply validation.

The prompt-isolation tests are the important ones here: the specification requires
that the official answer key never reaches a judge, and "we remembered to delete
the field" is not a guarantee. These tests check a property of the types
(``JudgeView`` has no key field) and a property of the rendering (changing the key
changes nothing), which together do not depend on anybody remembering anything.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from medeval_study.judging import MockJudgeTransport, parse_verdict
from medeval_study.models import JudgeView, McqItem, normalise_stem
from medeval_study.prompts import SYSTEM_PROMPT, USER_TEMPLATE, render_prompt
from medeval_study.responses import TEMPLATES, build_candidate_response, choose_distractor
from medeval_study.sampling import CONDITION_CORRECT, CONDITION_INCORRECT, select_items

OPTIONS = {
    "A": "Acute myocardial infarction",
    "B": "Unstable angina pectoris",
    "C": "Pulmonary embolism",
    "D": "Aortic dissection",
}


def make_item(item_id: str, *, key: str = "A", row_index: int = 0, question: str | None = None) -> McqItem:
    text = question or (
        f"A 54-year-old man presents with crushing central chest pain radiating to the left arm. "
        f"Electrocardiography and troponin are requested. Case {item_id}."
    )
    return McqItem(
        item_id=item_id,
        question=text,
        options=dict(OPTIONS),
        key_letter=key,
        key_text=OPTIONS[key],
        dataset="synthetic",
        split="dev",
        row_index=row_index,
        meta={},
    )


# --------------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------------- #
def test_sampling_has_no_duplicate_stems() -> None:
    items = [make_item(f"i{index}", row_index=index) for index in range(10)]
    items.append(make_item("dup", row_index=99, question=items[0].question))
    plan = select_items(items, n=5, seed=7)
    stems = [normalise_stem(row.item.question) for row in plan.items]
    assert len(stems) == len(set(stems))
    assert plan.discarded_duplicate_stems == 1


def test_sampling_is_balanced_and_reproducible() -> None:
    items = [make_item(f"i{index}", row_index=index) for index in range(40)]
    first = select_items(items, n=10, seed=11)
    second = select_items(items, n=10, seed=11)
    assert [row.item.item_id for row in first.items] == [row.item.item_id for row in second.items]
    assert first.condition_counts[CONDITION_CORRECT] == 5
    assert first.condition_counts[CONDITION_INCORRECT] == 5


def test_sampling_condition_is_not_the_draw_order() -> None:
    """Conditions must not correlate with position in the sampled list."""
    items = [make_item(f"i{index}", row_index=index) for index in range(60)]
    plan = select_items(items, n=20, seed=3)
    first_half = [row.condition for row in plan.items[:10]]
    assert first_half.count(CONDITION_CORRECT) in range(3, 8), "conditions look ordered by draw position"


def test_sampling_refuses_when_pool_is_too_small() -> None:
    items = [make_item(f"i{index}", row_index=index) for index in range(3)]
    with pytest.raises(ValueError, match="distinct stems"):
        select_items(items, n=5, seed=1)


def test_sampling_seed_changes_the_draw() -> None:
    items = [make_item(f"i{index}", row_index=index) for index in range(50)]
    a = {row.item.item_id for row in select_items(items, n=10, seed=1).items}
    b = {row.item.item_id for row in select_items(items, n=10, seed=2).items}
    assert a != b


# --------------------------------------------------------------------------- #
# Candidate responses
# --------------------------------------------------------------------------- #
def test_correct_condition_names_the_key_option() -> None:
    item = make_item("i1", key="C")
    response = build_candidate_response(item, condition=CONDITION_CORRECT, seed=5)
    assert response.chosen_letter == "C"
    assert response.chosen_text == item.key_text
    assert "C" in response.text


def test_incorrect_condition_never_names_the_key_option() -> None:
    for index in range(30):
        item = make_item(f"i{index}", key="B", row_index=index)
        response = build_candidate_response(item, condition=CONDITION_INCORRECT, seed=index)
        assert response.chosen_letter != item.key_letter
        assert response.chosen_text != item.key_text


def test_distractor_prefers_lexical_overlap() -> None:
    """The wrong option that shares vocabulary with the key is the plausible one."""
    item = McqItem(
        item_id="i1",
        question="A synthetic question long enough to pass the loader filter, with four options.",
        options={
            "A": "TNF-alpha secretion by CD4+ T cells",
            "B": "IL-5 secretion by Th2 cells",
            "C": "Antigen binding to IgM",
            "D": "MHC I binding to CD8+ T cells",
        },
        key_letter="A",
        key_text="TNF-alpha secretion by CD4+ T cells",
        dataset="synthetic",
        split="dev",
        row_index=0,
    )
    choice = choose_distractor(item, seed=1)
    assert choice.letter == "B", "B shares 'secretion' and 'cells' with the key"
    assert choice.reason == "lexical_overlap_with_key"
    assert choice.overlap > 0


def test_distractor_never_returns_the_key_option() -> None:
    item = make_item("i1", key="A")
    choice = choose_distractor(item, seed=1)
    assert choice.letter != "A"
    assert choice.reason in {"lexical_overlap_with_key", "no_lexical_overlap_seeded_choice", "lexical_overlap_tie_broken_by_seed"}


def test_templates_are_shared_across_conditions() -> None:
    items = [make_item(f"i{index}", row_index=index) for index in range(60)]
    correct = {build_candidate_response(item, condition=CONDITION_CORRECT, seed=2).template_id for item in items}
    incorrect = {build_candidate_response(item, condition=CONDITION_INCORRECT, seed=2).template_id for item in items}
    assert correct == incorrect, "a template used for one condition but not the other would leak the condition"
    assert correct == {template_id for template_id, _ in TEMPLATES}


def test_templates_contain_only_letter_and_text_slots() -> None:
    for _, template in TEMPLATES:
        formatted = template.format(letter="A", text="example")
        assert "{" not in formatted


# --------------------------------------------------------------------------- #
# Key isolation
# --------------------------------------------------------------------------- #
def test_judge_view_has_no_key_field() -> None:
    fields = set(JudgeView.__dataclass_fields__)
    assert fields == {"item_id", "question", "options"}
    assert not any("key" in name or "answer" in name or "gold" in name for name in fields)


def test_prompt_is_byte_identical_when_the_key_changes() -> None:
    """Same question, options and candidate text; different official key."""
    response_text = "The best answer is A: Acute myocardial infarction."
    prompts = set()
    for key in ("A", "B", "C", "D"):
        item = make_item("i1", key=key)
        prompts.add(render_prompt(item.judge_view(), response_text))
    assert len(prompts) == 1


def test_prompt_contains_no_key_vocabulary() -> None:
    prompt = render_prompt(make_item("i1", key="C").judge_view(), "Answer: A (Acute myocardial infarction).")
    lowered = prompt.lower()
    for banned in ("answer key", "answer_key", "key_letter", "ground truth", "correct answer is", "label:"):
        assert banned not in lowered
    assert "A) Acute myocardial infarction" in prompt


def test_render_prompt_only_reads_question_and_options() -> None:
    """A minimal duck-typed view is enough: no key field is ever consulted."""
    import types

    view = types.SimpleNamespace(question="Q?", options={"A": "a", "B": "b", "C": "c", "D": "d"})
    prompt = render_prompt(view, "Answer: A")  # type: ignore[arg-type]
    assert "Q?" in prompt and "A) a" in prompt


def test_system_and_template_do_not_mention_the_key() -> None:
    assert "key" not in SYSTEM_PROMPT.lower()
    assert "key" not in USER_TEMPLATE.lower()


def test_mock_judge_never_reports_an_unregistered_prompt() -> None:
    transport = MockJudgeTransport(oracle={})
    completion = transport.complete(
        [{"role": "user", "content": "a prompt nobody registered"}],
        temperature=0.3,
        seed=1,
        max_tokens=10,
        timeout_s=1,
    )
    assert completion.error is not None


# --------------------------------------------------------------------------- #
# Reply validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "ok", "verdict", "confidence"),
    [
        ('{"verdict": "CORRECT", "confidence": 80}', True, "CORRECT", 80),
        ('{"verdict": "incorrect", "confidence": 20}', True, "INCORRECT", 20),
        ('Sure!\n```json\n{"verdict": "CORRECT", "confidence": 0}\n```', True, "CORRECT", 0),
        ('{"verdict": "CORRECT", "confidence": "75"}', True, "CORRECT", 75),
        ('{"verdict": "CORRECT", "confidence": 75.4}', True, "CORRECT", 75),
        ('{"verdict": "CORRECT"}', False, None, None),
        ('{"verdict": "MAYBE", "confidence": 50}', False, None, None),
        ('{"verdict": "CORRECT", "confidence": 140}', False, None, None),
        ('{"verdict": "CORRECT", "confidence": -1}', False, None, None),
        ('{"verdict": "CORRECT", "confidence": true}', False, None, None),
        ("no json here", False, None, None),
        ("", False, None, None),
    ],
)
def test_parse_verdict_contract(text: str, ok: bool, verdict: str | None, confidence: int | None) -> None:
    parsed = parse_verdict(text)
    assert parsed.ok is ok
    assert parsed.verdict == verdict
    assert parsed.confidence == confidence
    if not ok:
        assert parsed.error


def test_mock_transport_is_deterministic_for_the_same_prompt() -> None:
    prompt = "Question: X\n\nOptions:\nA) a\n\nCandidate answer:\ny"
    digest = __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest()
    transport = MockJudgeTransport(oracle={digest: True}, accuracy=0.5, seed=4)
    first = transport.complete([{"role": "user", "content": prompt}], temperature=0.3, seed=1, max_tokens=10, timeout_s=1)
    second = transport.complete([{"role": "user", "content": prompt}], temperature=0.3, seed=1, max_tokens=10, timeout_s=1)
    assert first.text == second.text


def test_sampling_is_stable_across_input_order() -> None:
    items = [make_item(f"i{index}", row_index=index) for index in range(30)]
    shuffled = list(items)
    random.Random(0).shuffle(shuffled)
    a = [row.item.item_id for row in select_items(items, n=8, seed=5).items]
    b = [row.item.item_id for row in select_items(shuffled, n=8, seed=5).items]
    assert sorted(a) == sorted(b)


def test_item_serialisation_keeps_key_out_of_judge_view() -> None:
    item = make_item("i1", key="D")
    assert "key_letter" in item.as_dict()
    assert "key_letter" not in item.judge_view().as_dict()
    assert dataclasses.replace(item, key_letter="A").judge_view() == item.judge_view()
