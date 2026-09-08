# Earth Health method

Earth Health is a fixed-composition 1–100 index. **100 = best condition / lowest pressure.**

## Comparable historical series

The configured historical floor is 1993. Every displayed year uses the same enabled family backbones, the same family weights, and the same normalization rules. `build_earth_health_index.py` intersects valid annual years across all nine families, requires 1993 to exist in every family, and then emits a continuous annual series until the first common-year gap. The headline score is the final year in that exact same series.

There is no mixed-year `LATEST` score.

## Family backbones

See `HISTORICAL_BACKBONES.md`. Each family intentionally has a small long-running global scoring backbone. Richer/younger datasets are diagnostics only and receive zero Earth Health weight.

## Aggregation

Family scores are combined using a weighted geometric mean. Default governance weights are Biodiversity 14%, Habitat 12%, Vegetation 8%, Pollution 12%, Climate forcing 15%, Cryosphere 9%, Global Ocean 13%, Freshwater 11%, Ozone 6%.

Weights are explicit policy choices, not immutable scientific constants.

## Missing data

Missing data is never interpreted as healthy. Historical Earth Health requires every enabled backbone family to have sufficient annual coverage for the year. A missing 1993 backbone makes the build fail; a later missing year stops the comparable timeline.

## Diagnostics

Current diagnostic files live under `frontend/public/data/indices/diagnostics/`. They may contain newer measurements than the headline Earth Health year. The frontend labels them **not scored**. They are useful for explaining the current state without altering the historical formula.
