# Recommendation menu — the ONLY recommendations agents may issue

<!-- HARRY: the rails for A2. Each entry: the recommendation type, the computed
     condition that makes it ELIGIBLE (deterministic code checks this, not the
     agent), and the evidence the write-up must cite. The agent selects/orders/
     justifies from eligible entries only — it can never invent a type or a number. -->

| type | eligible when (computed) | must cite |
|---|---|---|
| `shift_within_type` | two audiences of the SAME audience_type differ ≥2x on cost/claimed conv with material spend | both costs, both spends, platform-claimed label |
| `fix_naming` | unparsed-name spend share above threshold | the % of spend, the offending names |
| `investigate_tracking` | claim ratio outside the normal band (see conversion_types.md) | the ratio, the affected channel |
| `rebalance_channel_budget` | MMM exists AND allocator output differs from current split | allocator numbers ONLY (never the agent's own), the ROI intervals |
| `scale_with_test` | MMM ROI interval entirely above 1 AND spend below saturation midpoint | interval bounds, saturation position |
| `cut_or_restructure` | MMM ROI interval entirely below 1 | interval bounds |
| `unlock_mmm` | no business-KPI series exists | what matchback would add |

Approved by Harry 2026-07-12. Rules: **max 3 recommendations per report**; ordered by
money at stake; every one labeled with its evidence grade (platform-claimed /
analytics-measured / modeled).
