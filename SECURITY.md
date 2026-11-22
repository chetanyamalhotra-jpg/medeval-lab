# Security policy

medeval-lab is a **research tool**, not a medical device and not a service. Read
the threat model below before you point it at anything other than your own
machine. The short version: it is built for **one user, on one trusted local
computer, over loopback**, and it has no TLS, no accounts and no multi-user
authorisation.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.5.x | ✅ security fixes |
| < 0.5 | ❌ please upgrade; those releases had no bring-your-own-key audit |

Only the `main` branch of this repository is supported. A release zip is exactly
the tracked content; there is no server component and nothing is hosted.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

- Preferred: GitHub's **private vulnerability reporting** for this repository
  (Security → Report a vulnerability). Keep it enabled if you are the maintainer.
- Otherwise: email `301152462+chetanyamalhotra-jpg@users.noreply.github.com` (also listed in
  `.github/allowed-emails.txt`).

Include the version, the platform, what an attacker controls (a provider response,
a web page, a local account, a malicious base URL, ...) and the smallest
reproduction you can. Please do not include a real API key; rotate one before you
paste anything near a bug report.

Reports are reviewed on a best-effort basis; no fixed response-time SLA is
promised.

## Threat model

**In scope, defended:**

| Threat | Defence |
| --- | --- |
| Another website in your browser calling the app's API on `127.0.0.1` | Per-launch random token required in `X-Medeval-Token`; it arrives in the URL fragment (never sent to the server, never logged), lives in `sessionStorage`, and is forgotten when the tab closes. `Host` and `Origin` are checked; there is no CORS header. |
| The key leaving the machine to a host you did not configure | `Authorization` is sent only to the exact configured host; HTTP redirects are **not followed**, so a `3xx` cannot move the header to another host (a fake server tests this). |
| A key hidden in a URL | `base_url` values with embedded credentials (`user:pass@host`) are refused. Only `http`/`https` are accepted; plain `http` to a non-loopback host warns. |
| A key leaking into logs, run files, reports, exports, caches | Keys live only in the server process's memory (`KeyStore`). Redaction is applied to logged and stored text; run manifests and serialized configs strip the key; the CLI takes keys **only** from an environment variable named in `connections.yaml` and refuses a `--api-key` argument (it would leak into shell history and process lists). |
| The app being reachable from the network | It binds to `127.0.0.1` only. A non-loopback `--host` is refused unless `--allow-remote` is passed, which prints a loud warning; the token, `Host` and `Origin` checks stay active. |
| A committed key | `.env*` (except `.env.example`), `.private/`, `hf/`, `connections.yaml` and local notes are git-ignored; `scripts/oss_audit.py` and `scripts/scan_secrets.py` fail the build on key-like strings, tracked `.env` files, personal paths and emails outside the allow-list. See `OSS_AUDIT.md`. |
| Silent spend | Dry run first; explicit confirmation before live calls; per-connection call and USD caps; a run-wide call cap; a worst-case estimate with no invented prices. |

**Out of scope, not defended:**

- **A compromised machine or user account.** Anything running as you can read the
  app's memory, your environment variables and your browser profile. The app
  cannot protect a key from the machine it is running on.
- **The provider.** Your key is sent to the endpoint you configure; the provider
  can see it and the prompts. Choose the base URL carefully — you are trusting it
  with both.
- **Network exposure.** There is no TLS, no authentication beyond the launch
  token, no rate limiting and no multi-user model. Do not put this behind a
  reverse proxy or expose it to a LAN/VPN/internet.
- **Prompt-injection style attacks through model output**, benchmark content or
  pasted notes. Judge output is parsed defensively but is not a security boundary.
- **Denial of service, timing side channels and local-state tampering.** The app is
  a single-user local tool and does not defend against itself.
- **Third-party dependencies** beyond running Dependabot and the audit in CI. See
  `.github/dependabot.yml` and the CI workflow.

## Data and network behaviour

- **No telemetry, ever.** The app does not phone home, check for updates or
  collect usage. Outbound requests are limited to
  (a) provider base URLs that you configured, and (b) metadata lookups you start
  yourself (the Papers page, which announces the lookup in the UI before doing
  it, plus `medeval data fetch`, which downloads a public dataset under its own
  licence).
- **Dry runs make no network calls at all.** They use labelled mock judges and,
  when no dataset is cached, a built-in synthetic sample marked
  `PIPELINE TEST ONLY`.
- **Benchmark text is not redistributed.** Dataset items are downloaded by you,
  stay in a git-ignored cache, and are not committed, reported or exported (only
  ids, verdicts, hashes and aggregates are).
- **Local state location:** run directories under `results/study_runs/`,
  connection metadata in `data/connections.json` (no keys), preferences and local
  notes under `data/`. Delete the folder to reset.

## Key-handling guarantees, precisely

1. A key is never written to disk by this project's code. The app's `KeyStore`
   keeps it in memory; the CLI reads it from the environment variable named by
   `key_env` and passes it to the HTTP client in memory.
2. A key is never printed by this project's code: not by `doctor`, not by the CLI
   plan, not in error messages, not in the tests' assertions (the tests assert the
   key is *absent* from output).
3. A key is sent only to the configured host, only over the configured scheme,
   only for the calls you confirm.
4. What this cannot guarantee: the provider, the operating system, swap files,
   crash dumps, shell history (there is no `--api-key` argument for this reason),
   a browser extension, or anything else on your machine.

## If you think a key has leaked

1. Revoke/rotate it at the provider immediately. The maintainers cannot revoke it
   for you and cannot see your usage.
2. Delete the local `.env` file and any copy of it, and any exported run bundle
   that may contain it.
3. Run `python scripts/scan_secrets.py` and `python scripts/oss_audit.py` in the
   clone, and open a private report per the section above.
