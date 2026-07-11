# Metrics playbook — definitions, healthy ranges, classic misreadings

<!-- HARRY: per metric, in your words. The spec agent uses this to set targets
     (reporting.targets shape: goal / good / warn) when the client brief doesn't;
     the commentary agent uses the misreadings to avoid writing them. -->

Format per metric:

```
### <metric key from config/metrics.yaml>
- What it is / what it is NOT:
- Healthy range by context (search vs social vs B2B):   <- becomes good/warn bands
- Classic misreading to avoid:
```

### cpm
- TODO(Harry)

### cpc
- TODO(Harry)

### ctr
- TODO(Harry): incl. why search CTR and social CTR must never share a target.

### cost_per_key_event
- TODO(Harry)

### roas
- What it is NOT: proof of incrementality. Platform revenue / spend only.
- TODO(Harry): ranges.

### engagement_rate / pages_per_session / vtr
- TODO(Harry)
