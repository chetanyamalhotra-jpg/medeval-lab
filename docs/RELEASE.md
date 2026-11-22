# Release safeguards

Before any push, run the offline scanner from the repository root:

```powershell
..venv\Scripts\python.exe scripts\scan_secrets.py
```

The scanner reports only pass/fail categories and paths; it never prints a
matched value. It scans current tracked files and all reachable Git-history
blobs. It also rejects tracked or historical `.env`, `.private/`, `data/cache/`,
`hf/`, and `calls.jsonl` paths.

Install the local pre-push hook once (the hook itself is intentionally not
versioned):

```powershell
@'
#!/bin/sh
exec python scripts/scan_secrets.py
'@ | Set-Content -NoNewline .git/hooks/pre-push
```

The release workflow is offline and forces the mock backend. Do not add API keys
or other repository secrets to GitHub Actions.
