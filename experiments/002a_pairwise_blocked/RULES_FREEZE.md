# Rule judge freeze record

Frozen rule file: `packages/medeval_judges/rules/contraindications.yaml` version
`v0.1`, SHA-256:

`dbb62b978cbc2d1b9b65b59ae50e68cc5dc5ccdfba6c52a7689fa2a0758ab018`

This record must be committed and tagged `rules-v0.1-freeze` before the held-out
rule score is generated. The held-out cases were authored for v0.2 and must not be
used to tune this frozen version. Negation and weight-based dosing (mg/kg,
units/kg/hour) remain known limitations until a separately versioned post-hoc rule
set is evaluated and reported before/after.
