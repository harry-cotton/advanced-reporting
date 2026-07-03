# Campaign Naming Convention — Generator (prototype, Plan model)

Generates consistent campaign / ad set / ad names — plus matching UTM-tagged landing
URLs — from a simple Excel template. Built in Cowork as a prototype for Advanced Reporting.

## Why this exists
Half the battle in reporting is decoding what an exported campaign name *means*. A naming
convention is a grammar; if names are **generated** from that grammar, the platform data
can later be **decoded** back into fields with no guesswork. This is the encode half — the
same scheme is meant to drive a future decoder/parser.

## Files
- `naming_generator.py` — the tool (only dependency: `openpyxl`).
- `naming_template.xlsx` — the fill-in template (README / Plan / Scheme / Settings).
- `naming_output.xlsx` — example output generated from the template.

## Use
```bash
pip install openpyxl
copy naming_template.xlsx my_test.xlsx        # keep the example; edit the copy
python naming_generator.py generate my_test.xlsx my_output.xlsx
```

## The Plan model (the important bit)
You fill in **one row per real line item** on the **Plan** sheet. Each cell is either a
single value or a **comma-list**. Comma-lists expand **within that row only**, and totals
**sum across rows** — so there's no global cross-product explosion. You list only the
combinations you actually want.

Row volume = the product of the comma-list lengths in that row (single-value cells = ×1).
The example Plan = 2 + 2 + 1 + 2 = **7 ads** from 4 lines:

| audience_detail | format | size | placement | → ads |
|---|---|---|---|---|
| INT-FITNESS | VID | 9x16 | FEED, REELS | 2 |
| LAL-1PCT | STATIC | 1x1, 4x5 | FEED | 2 |
| CART-ABANDON | VID | 9x16 | FEED | 1 |
| PDP-VIEW-30D | VID, STATIC | 9x16 | REELS | 2 |

## Other sheets
- **Scheme** — how each level's name is composed, in order, with a delimiter. Default Ad Set
  = `audience_type + audience_detail + placement` (e.g. `RETARGET_CART-ABANDON_REELS`).
- **Settings** — casing, max name length, a combination guardrail, landing URL, utm_medium.

## Output
- **Trafficking Sheet** — one row per ad: every field + Campaign / Ad Set / Ad names + a
  UTM-tagged landing URL + an Ad Set Key for pivots.
- **Validation** — flags illegal characters, over-length names, duplicate combinations.
- **Summary** — totals + per-channel counts (live formulas).

## Ties into the rest of the project
Channel codes (META, TIKTOK, …) map to canonical channels (meta, tiktok, …) — the same
vocabulary as the repo's `channel_aliases` — and to `utm_source`, so the generated UTMs
line up with GA4 (sessionSource / sessionCampaignName) on the way back in. Natural next
step: the matching **decoder** that parses exported names back into these fields.
