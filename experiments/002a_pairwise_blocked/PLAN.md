# Experiment 002 — real judges (pre-registration)

## Status

Prepared before any real-model call. The implementation rejects a mock backend,
requires a model ID returned by the configured provider's `/models` endpoint, and
requires a three-call smoke stage to finish before the full run.

## Question and hypotheses

On 70 synthetic, author-labelled pairwise cases (50 v0.1 cases plus 20 held-out
v0.2 cases), can each specified judge identify the response containing the
planted error above chance? The planted error is the reference label. This does
not test clinical safety or establish a clinician reference standard.

H1: each reference-free and reference-guided DeepSeek configuration will have
accuracy estimable against planted truth. H2: reversing presentation order may
change decisions; this is measured rather than assumed absent. H3: zero-
temperature calls can still vary, so three repeated calls will quantify observed
nondeterminism. The rule judge is evaluated separately on the pre-freeze held-out
set; its v0.1 result remains an upper bound because those rules saw v0.1 cases.

## Judge configurations

1. `deepseek_free`: reference-free rubric prompt.
2. `deepseek_guided`: reference-guided rubric prompt.
3. `rule_frozen`: deterministic v0.1 rules, frozen before held-out case creation.
4. `jury_three_member`: majority of the two DeepSeek configurations and the rule
   judge. A tie is recorded as a tie (not silently converted to a win).

There is only one remote provider/model family. The two DeepSeek configurations
are not independent jurors; self-preference and correlated errors cannot be
separated from model quality. This is not a cross-family jury. A local small model
is included only if a local endpoint is actually detected and recorded.

## Sample and calls

Each case is evaluated in both presentation orders. At temperature 0, each order
is repeated three times. A separate, explicitly configured temperature-above-zero
run provides a self-consistency check. Calls are cached on model, prompt hash,
temperature and seed; only cache misses count toward the cap. The dry run emits
the exact planned call count and maximum-token estimate before any call.

## Metrics and analysis

For each judge and each reference standard, report accuracy with deterministic
bootstrap confidence intervals; raw agreement, Cohen's kappa and Gwet AC1;
presentation-order/position bias; response-length association; parse and API
failure counts; and examples where the judge differs from planted truth. Compare
configurations with McNemar tests labelled exploratory and multiplicity-adjusted.
Report the rule judge's v0.1 and held-out accuracy separately. No composite score
and no table mixing mock with real results are permitted.

## Stopping rules and failure criteria

Stop before a paid request if model discovery fails, the configured model is not
returned, the call cap or estimated USD cap would be exceeded, or pricing is
unverified and the caller elects not to rely on the call cap. Stop cleanly when a
guard trips. Stop the full run if any of the first three smoke calls produces an
API or JSON-parse failure; preserve those failures as failures, not wrong answers.
Failure includes unusable JSON at a material rate, a nonzero API failure rate,
guard termination, insufficient evaluable calls, or results that cannot support
the planned metrics. A finding of no detectable bias is not evidence of no bias.

## What the results can and cannot support

They can support measurements of the configured model on author-written,
planted-error cases. They cannot support clinical validation, real-world safety,
answer-key correctness, general model ranking, or independent cross-family jury
validity.
