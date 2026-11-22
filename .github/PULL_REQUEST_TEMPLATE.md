## Summary

<!-- One or two sentences: what does this pull request do? -->

## What changed

<!-- Bullet points: files, packages, behaviour. Keep it short. -->

## Why

<!-- The problem this solves, or the issue it closes (for example: Closes #123). -->

## How tested

<!-- Paste the commands you ran and a short result. -->

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe scripts\oss_audit.py
.\.venv\Scripts\python.exe scripts\scan_secrets.py
```

<!--
POSIX equivalents:
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/python scripts/oss_audit.py
.venv/bin/python scripts/scan_secrets.py
-->

## Checklist

- [ ] No secrets: no API keys, tokens, `.env` contents or credentials in code, tests, logs, issues or this PR.
- [ ] No benchmark item text, candidate text or reviewer notes committed.
- [ ] Tests added or updated for behaviour changes, and the existing tests still pass.
- [ ] Documentation updated where behaviour changed.
- [ ] `pytest`, `ruff` and `scripts/oss_audit.py` pass locally (CI runs the same checks where it is enabled).
- [ ] A dry run passes, for example `.\.venv\Scripts\python.exe -m medeval run exp002 --dry-run`.
- [ ] `results/` and `experiments/` evidence is untouched.
- [ ] No git history rewrite (no force-push, no amended published commits).
- [ ] No clinical claims: medeval-lab is a research tool, not a medical device.
