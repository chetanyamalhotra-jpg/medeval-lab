# How to add your own items

You do not need to write code to add items. You add JSON, then run one command.

## 1. Copy the template

Copy `data/items/template_item.json`, rename it (for example
`data/items/my_items_v0.1.json`), and make it a JSON **array**:

```json
[
  { "...": "first item" },
  { "...": "second item" }
]
```

A `.jsonl` file with one object per line also works.

## 2. Fill in every field

| field | what it means |
|---|---|
| `id` | unique, lowercase, e.g. `cardio-0001` |
| `text` | the vignette in **your own words**, ending in a question |
| `options` | 2-8 answer options, no letter prefixes; `[]` for an open-ended item |
| `gold_answer` | exactly one of the options (or the reference text for open items) |
| `label_source` | `paper_finding`, `guideline`, `planted_error`, `public_dataset_key` or `synthetic` |
| `provenance.doi` | the paper's DOI — required for `paper_finding`/`guideline`, must be `null` for synthetic/planted items |
| `provenance.section` | where in the paper the fact comes from (e.g. "Results, Table 2") |
| `provenance.licence` | the source licence (SPDX, e.g. `cc-by-4.0`) or `synthetic-original` |
| `provenance.extraction_date` | the date you extracted the fact (YYYY-MM-DD) |
| `publication_date` | paper date, or item creation date for synthetic items |
| `split` | `dev` while you iterate; `hidden` for items you will not look at again |
| `version` | `v0.1` (bump only when the item meaning changes) |
| `review_status` | `single-rater, author-reviewed` while only you have checked it |
| `human_reference` | keep `null` — there is no clinician workflow in v0.1 |

## 3. Rules that the validator enforces

- No provenance, no label source, or no licence → **rejected**.
- `gold_answer` must match one option exactly.
- Synthetic items may not carry a DOI.
- Paper items must carry a DOI.
- Duplicate ids are rejected; duplicate wording is flagged as a warning.
- Options must not contain `A.` style prefixes (the harness adds its own).

## 4. Validate and use

```powershell
.\.venv\Scripts\python.exe -m medeval validate data/items/my_items_v0.1.json
```

Then point a judge or an experiment at the file. Nothing is ever silently
altered: the validator reports issues and leaves your file untouched.

## 5. What you may NOT do (and why)

- **Do not copy sentences from papers.** Facts are not copyrightable; sentences
  are. Restate the fact in your own words and cite the source.
- **Do not claim a DOI you have not opened.** Verify it resolves before setting
  `label_source: paper_finding`. Unverifiable citations belong in `VERIFY_ME.md`.
- **Do not label items clinically validated.** One author's review is
  "single-rater, author-reviewed", which is exactly what the field says.
- **Do not put patient data in this repository.** Public or synthetic data only.
