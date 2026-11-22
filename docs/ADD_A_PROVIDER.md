# Add a provider preset

Add a provider to the local app's preset list: a base URL and documentation links,
nothing else. medeval-lab is a research tool, not a medical device: it has no
clinical validation, no clinician has reviewed any content, and small-sample results
are exploratory.

A preset is convenience, not configuration. It fills an editable base URL and links
to the provider's own documentation; it does not ship a model id, a key or a price.
The owner still chooses the model, pastes the key (kept in the app's process memory
only) and sets the caps and optional prices per connection. Nothing about a preset
is vendor verification, and a preset is not an endorsement of any provider.

## Where presets live

- `PRESETS` in `packages/medeval_web/connections.py` is the only list. It is served
  by `GET /api/presets` and rendered generically in the Models page, so adding an
  entry needs no other code change.
- The module docstring at the top of that file keeps a source list: each provider,
  the official documentation URL the base URL was read from, the pricing URL, and
  the date it was last checked.
- `VERIFY_ME.md` records the same verification for the current release (the row that
  lists the preset base URLs and their sources).

## Fields

| Field | Meaning | Rule |
| --- | --- | --- |
| `label` | Name shown in the Models page dropdown. | Short and unambiguous. |
| `base_url` | The OpenAI-compatible base URL. | From the provider's official documentation only. Must start with `http://` or `https://` and must not embed credentials (`user:pass@host`). |
| `api_style` | Which request shape the endpoint speaks. | `"chat"` (chat completions) or `"responses"` (Responses API). |
| `requires_key` | Whether the endpoint needs an API key. | `True` for remote providers; `False` for a loopback model server. |
| `docs_url` | The official page that documents the endpoint. | Empty only for the generic `custom` entry. |
| `pricing_url` | The provider's official pricing page. | Empty only when the provider has no pricing page (for example a local server). Never ship a price. |
| `last_verified` | The date you last read the documentation and confirmed the base URL. | `YYYY-MM-DD`. That is the date of the *reading*, not the date you typed the entry. |

Rules that matter:

- **No price may be shipped.** Price fields start empty on every connection, and the
  preset carries `pricing_url` so the owner can open the provider's own price page.
  The app never fetches, guesses or stores a price. If a connection declares no price,
  the estimate says "unavailable, call cap is binding" and the call cap is the
  enforced limit. A stale hard-coded price is worse than no price.
- **Official documentation only.** Both URLs must be the provider's own docs; do not
  link a third-party blog, a reseller, or a guess. If the provider publishes no
  pricing page, leave `pricing_url` empty rather than inventing one.
- **Everything stays editable.** The base URL and links only prefill the form; a
  moved or wrong URL is a user edit, not a code change.
- **Do not add defaults.** No default model id, no default key, no key field, no
  `price_*` field, and no provider-specific branch elsewhere in the app.

## Steps

1. Read the provider's official API documentation and find the OpenAI-compatible
   base URL, the request style (`chat` or `responses`), and the pricing page. Note
   the date you read them.
2. Add an entry to `PRESETS` in `packages/medeval_web/connections.py`. The shape is:

   ```python
   "exampleprovider": {
       "label": "Example Provider",
       "base_url": "https://api.example.com/v1",
       "api_style": "chat",
       "requires_key": True,
       "docs_url": "https://docs.example.com/api/overview",
       "pricing_url": "https://docs.example.com/pricing",
       "last_verified": "YYYY-MM-DD",
   },
   ```

   Keep the entry in the same order as the existing presets and keep it a plain
   dict with exactly these keys.
3. Update the provider source list in the module docstring at the top of
   `connections.py`: provider name, the official docs URL, the pricing URL, and the
   date. This list is how a reader checks that a base URL was read rather than
   guessed.
4. Keep the no-price assertions honest. The tests are in
   `tests/test_v050_byok.py`:
   - `test_presets_ship_no_prices_and_carry_verification_metadata` loops over every
     preset and asserts that no `price_in_per_mtok`/`price_out_per_mtok` key exists,
     that `pricing_url` and `last_verified` are present, and that every preset except
     `custom` has a pricing URL or is the keyless local `ollama` entry.
   - `test_presets_endpoint_ships_no_price_defaults` asserts the API endpoint returns
     `prices_default: null` for every preset.

   A new key is covered by those loops automatically. If the new preset needs an
   exception (for example, it has no official pricing page), update the corresponding
   assertion in `tests/test_v050_byok.py` the same way the keyless local `ollama`
   entry is handled. Never add a price or a fake URL to make a test pass.
5. Update `VERIFY_ME.md` with the same sources and read date (the preset row there is
   the release's verification record).
6. Run the checks from the repository root:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests\test_v050_byok.py
   .\.venv\Scripts\python.exe -m pytest
   .\.venv\Scripts\ruff.exe check .
   .\.venv\Scripts\python.exe scripts\oss_audit.py
   .\.venv\Scripts\python.exe scripts\scan_secrets.py
   ```

7. Check it by hand without spending anything:

   ```powershell
   .\.venv\Scripts\python.exe -m medeval ui
   ```

   Open **Models → Add a connection**, pick the new preset, and confirm that the
   base URL and API style are filled and that the price fields stay empty. The
   preset's `docs_url`, `pricing_url` and `last_verified` are returned by
   `GET /api/presets` and asserted by the tests. Press **Test** on the saved
   connection to list the endpoint's models; that call spends nothing (only the
   separately confirmed one-token call costs anything). A preset change does not
   need a live model run to validate.

## What a preset change should not include

- A price, or a guessed price range.
- A default model id, a default key, or a key field.
- Credentials in the base URL, a non-`http(s)` scheme, or a link shortener.
- A provider-specific branch in the app, the runner or the CLI; the generic
  connection path already handles every OpenAI-compatible endpoint.
- Test changes that weaken the "no price is shipped" assertions.

## Related documentation

- [BYOK_QUICKSTART.md](BYOK_QUICKSTART.md) — the user-facing path for adding a
  connection and key.
- [USER_GUIDE.md](../USER_GUIDE.md) — the Models page, Test connection and caps in
  detail.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — setup and the checks expected before a
  pull request.
- [VERIFY_ME.md](../VERIFY_ME.md) — what is verified, and where the current preset
  sources are recorded.
- [SECURITY.md](../SECURITY.md) — keys, loopback binding and private reporting.
