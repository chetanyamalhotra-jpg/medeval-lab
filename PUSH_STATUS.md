# Push status

**Nothing has been pushed and CI has never run.**

As of the v0.5.0 preparation, the work is committed locally on `main` and an
`origin` remote is configured, but no commit has ever been sent to it. That is
why no document in this repository claims that CI passed: see `VERIFY_ME.md` for
what has been verified by running it and what has not.

The owner's release steps — revoking keys, choosing a history strategy, enabling
secret scanning and branch protection, tagging and announcing — are in
`RELEASE_OSS_CHECKLIST.md`. This file is kept only as a record that the earlier
"push, then watch CI" step was never taken.
