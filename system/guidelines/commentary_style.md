# Commentary style guide

How reporting commentary should read — the structure, voice, and phrasing to emulate.
Distilled from analyst-written reports. Every number in commentary comes from the
computed FACTS provided to the agent; this guide governs *how* to say it, never *what*
the numbers are.

## Report structure (sections, in order)

1. **Exec summary** — one tight paragraph leading with the headline movement *and its
   driver*: the primary outcome (e.g. applications / conversions) and spend direction
   month-over-month, and the resulting cost-per-outcome direction. State the cause, not
   just the number ("spend decreased while volume held, driving CPA more efficient").
2. **Breakdown by segment** (position / product / audience) — for each major segment, in
   descending volume: the volume with the prior value in parentheses, its share of the
   total, and its CPA with the MoM change.
3. **Period-over-period notes (YoY / PoP)** — comparisons with *explicit caveats*: flight
   or launch timing, budget ramps, channels not live in the comparison period. Never
   present two periods as like-for-like without flagging why they aren't.
4. **Channel learnings** — per channel, the concrete signal: top geos by clicks, top
   keywords with CTR, event-driven spend, notable MoM moves.
5. **Funnel strategy** — the role each stage played (upper = prospecting / lookalike
   scale; mid = retargeting refinement; lower = high-intent conversion) and the takeaway
   that ties tactics to the funnel.
6. **Tactic-level PoP** — per tactic (channel × prospecting/retargeting): spend Δ%,
   conversion-volume Δ%, CPA-efficiency Δ%, position vs benchmark, and a one-line *why*.

## Voice

- Lead with the outcome and its driver; the number supports the sentence, it isn't the
  sentence.
- Always pair **direction + magnitude**: "increased slightly", "up 20% MoM",
  "CPA efficiency improved 7%".
- Show the **prior value in parentheses**: "$12.07 vs $9.59", "29,634 (up from 29,564)".
- **Attribute causes** explicitly and concretely: budget shifts, audience expansion,
  event support, flight dates, seasonality.
- **Compare to benchmark** when one exists: "30% under benchmark", "right at its $24 CPA
  benchmark".
- Define abbreviations on first use (MoM, PoP, YoY, CPA, CTR, LAL).
- Confident and specific — name the geos, keywords, and tactics; never vague.

## The CPA / cost-efficiency convention (read carefully)

- Lower cost-per-outcome = more efficient. "CPA efficiency increased / improved" means the
  CPA went **down**. Always make the good direction unambiguous so "increased" is never
  misread.
- Keep spend, volume, and cost together — a cost move is only meaningful beside its spend
  and volume moves ("spend +43%, conversions +26%, so CPA +14%").

## Phrasing patterns (fill from the computed facts)

- Segment: *"[Segment] accounted for N (up/down from M), [share]% of [outcomes], at a CPA
  of $X ([up/down] Y% MoM)."*
- Tactic PoP: *"[Channel] — [Prospecting/Retargeting]: spend [±X]%, conversion volume
  [±Y]%, CPA efficiency [±Z]% ($new vs $prior); [benchmark note]."*
- Caveat: *"[Comparison] looks [higher/lower], but [reason it isn't like-for-like]."*

## Do / don't

- **DO** quantify every claim; pair each metric move with a cause; flag part-period and
  launch-timing distortions; reference benchmarks; tie tactics back to the funnel.
- **DON'T** state a number without a direction or a prior; imply causation the data can't
  support; over-claim incrementality; or compare two periods without saying why they
  differ.

## Guardrails (non-negotiable, override style)

- Every numeral comes from the computed FACTS — never invent, round beyond the source, or
  estimate.
- Hedge causal language; keep platform-claimed and analytics-measured outcomes distinct
  and never sum them.
- If a driver isn't in the data, describe the movement without asserting a cause.
