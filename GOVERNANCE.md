# Governance

medeval-lab is a small, single-maintainer open-source project. This document
describes how decisions are made, in plain terms; it does not promise a
foundation, a committee or a process that does not exist.

## Maintainer

The project has one maintainer. The maintainer reviews pull requests, decides
what is merged, and prepares releases. Maintainer identity and contact details
are:

- Maintainer: Chetanya Malhotra (`@chetanyamalhotra-jpg`)
- Public contact: `301152462+chetanyamalhotra-jpg@users.noreply.github.com`
- Repository: `https://github.com/chetanyamalhotra-jpg/medeval-lab`

## Contributions

- Contributions arrive as pull requests and follow `CONTRIBUTING.md` and
  `.github/PULL_REQUEST_TEMPLATE.md`.
- CI is the automated check: lint, secret scan, tests and traceability when the
  workflow is enabled. A failing check blocks a merge; a passing check does not
  guarantee one, because the maintainer still reviews scope, honesty of claims
  and evidence handling.
- Changes to `results/` or `experiments/` are not accepted, because they are
  historical evidence and pre-registrations.

## Decisions and disagreements

- The maintainer decides. There is no vote, committee or foundation.
- If you disagree with a decision in a pull request, reply in that pull request.
  The maintainer will explain the reasoning there. Reasons may include scope,
  reproducibility, licensing, evidence integrity or clinical-claim risk.
- A decision can be revisited later with new evidence; reopening a discussion
  with facts is welcome, relitigating the same point is not.
- Decisions that change project-wide policy are recorded in the repository (for
  example in `DECISIONS.md` or `GOVERNANCE.md`) rather than only in a chat.

## Releases

- The maintainer prepares releases and writes the changelog.
- Releases are checked with `scripts/oss_audit.py` and `scripts/scan_secrets.py`
  and tagged from `main`; evidence under `results/` is not rewritten.
- Version numbers follow the changelog. Dates are set at release time.

## Security and conduct

- Security reports go privately per `SECURITY.md`; do not open a public issue
  for a vulnerability.
- Community behaviour is covered by `CODE_OF_CONDUCT.md`.

## What this project is not

This project has no clinical validation, no clinician review and no medical
device status. Governance decisions cannot grant it those things. Do not read a
merge, a release or a maintainer comment as a clinical endorsement.

## Response times

This project is maintained on a best-effort basis and makes no guaranteed
response-time promises.
