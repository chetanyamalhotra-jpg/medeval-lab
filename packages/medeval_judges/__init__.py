"""Pluggable judges for medical evaluation.

Four implementations of one protocol:

* :class:`~medeval_judges.exact_match.ExactMatchJudge` -- deterministic;
* :class:`~medeval_judges.rule.RuleJudge` -- deterministic rule file;
* :class:`~medeval_judges.llm.LLMRubricJudge` -- the shared OpenAI-compatible
  client, with blinded randomised order and reference-guided/free variants;
* :class:`~medeval_judges.jury.JuryJudge` -- majority vote across instruments.

Every verdict records backend, model, template version, seed, prompt hash and
timestamp, so a score can be audited later.
"""

from __future__ import annotations

from .base import (
    PairwiseCase,
    PairwiseJudge,
    PairwiseVerdict,
    PointwiseCase,
    PointwiseJudge,
    PointwiseVerdict,
    spread_verdicts,
)
from .exact_match import ExactMatchJudge, extract_choice
from .jury import JuryJudge
from .llm import LLMRubricJudge, MockPolicy
from .prompts import (
    PAIRWISE_TEMPLATE_VERSION,
    POINTWISE_TEMPLATE_VERSION,
    SYSTEM_PROMPT,
    render_pairwise,
    render_pointwise,
)
from .rule import DoseMention, RuleFile, RuleHit, RuleJudge, load_rules, parse_doses

__all__ = [
    "PAIRWISE_TEMPLATE_VERSION",
    "POINTWISE_TEMPLATE_VERSION",
    "SYSTEM_PROMPT",
    "DoseMention",
    "ExactMatchJudge",
    "JuryJudge",
    "LLMRubricJudge",
    "MockPolicy",
    "PairwiseCase",
    "PairwiseJudge",
    "PairwiseVerdict",
    "PointwiseCase",
    "PointwiseJudge",
    "PointwiseVerdict",
    "RuleFile",
    "RuleHit",
    "RuleJudge",
    "extract_choice",
    "load_rules",
    "parse_doses",
    "render_pairwise",
    "render_pointwise",
    "spread_verdicts",
]
