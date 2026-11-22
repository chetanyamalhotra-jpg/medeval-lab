"""Reliability and agreement statistics for medical AI evaluation.

Submodules:

* :mod:`medeval_reliability.agreement` -- Cohen, Fleiss, Krippendorff, ICC, AC1;
* :mod:`medeval_reliability.bootstrap` -- bootstrap intervals, McNemar, Wilson;
* :mod:`medeval_reliability.bias` -- position, length, self-preference probes
  and self-consistency across repeats.

Nothing here validates a clinical claim. These tools quantify how much a
measurement can be trusted as a measurement.
"""

from __future__ import annotations

from .agreement import (
    cohen_kappa,
    fleiss_kappa,
    gwet_ac1,
    icc,
    interpret_kappa,
    krippendorff_alpha,
    percent_agreement,
)
from .bias import (
    LengthBiasResult,
    PositionBiasResult,
    SelfPreferenceResult,
    length_bias,
    position_bias,
    repeat_agreement,
    self_preference,
)
from .bootstrap import (
    BootstrapCI,
    bootstrap_ci,
    cluster_bootstrap_ci,
    mcnemar_test,
    paired_bootstrap_diff,
    proportion_ci,
)

__all__ = [
    "BootstrapCI",
    "LengthBiasResult",
    "PositionBiasResult",
    "SelfPreferenceResult",
    "bootstrap_ci",
    "cluster_bootstrap_ci",
    "cohen_kappa",
    "fleiss_kappa",
    "gwet_ac1",
    "icc",
    "interpret_kappa",
    "krippendorff_alpha",
    "length_bias",
    "mcnemar_test",
    "paired_bootstrap_diff",
    "percent_agreement",
    "position_bias",
    "proportion_ci",
    "repeat_agreement",
    "self_preference",
]
