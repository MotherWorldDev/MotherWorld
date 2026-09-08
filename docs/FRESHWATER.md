# Global Freshwater Stability

Freshwater Stability is intentionally a **global-only** MotherWorld family in v1.

It is not calculated per terrestrial ecoregion and does not depend on which lakes/rivers happen to be rendered on the globe. Rivers, aquifers, watersheds, snowmelt systems and many lakes cross ecoregion boundaries; forcing those processes into terrestrial ecoregions would create arbitrary ownership and double-counting problems.

## Family components

Default component weights:

- **45% — Terrestrial water storage stability** — JPL GRACE/GRACE-FO Mascon CRI filtered terrestrial water-storage anomaly
- **30% — Root-zone soil-moisture stability** — ERA5-Land monthly soil water, layers 1–3 combined into an approximate 0–100 cm root zone
- **25% — Surface-water extent / permanent-water retention** — JRC Global Surface Water Yearly History

The family uses a weighted geometric mean. Missing components lower coverage and can suppress the family score.

## 1. GRACE terrestrial water storage

The builder area-weights the global land GRACE liquid-water-equivalent anomaly time series while excluding Antarctica and a broad Greenland mask. That exclusion prevents large ice-sheet mass loss from being counted again in both Freshwater and Cryosphere.

A calendar-month climatology is built from the configured baseline (default 2004–2014). Recent monthly values are expressed as standardized anomalies. The condition score is based on the recent mean absolute standardized departure:

- <= 0.5 standard deviations: 100
- >= 3 standard deviations: 0
- linear between those points

Both unusually wet and unusually dry persistent departures are treated as reduced *stability*. The raw global storage anomaly and trend remain visible as context.

## 2. Root-zone soil moisture

ERA5-Land monthly volumetric soil water layers are depth-weighted:

```text
0–7 cm     × 0.07
7–28 cm    × 0.21
28–100 cm  × 0.72
```

For every pixel and calendar month, the 1991–2020 P10/P90 envelope is computed. Recent land-area-months are classified as dry extreme, historical envelope, or wet extreme.

Under the reference climate, approximately 80% of observations fall inside P10–P90. Therefore:

```text
score = min(100, observed envelope fraction / 0.80 × 100)
```

This does not punish naturally dry climates; every pixel is judged against its own monthly historical distribution.

## 3. Global surface-water stability

JRC Global Surface Water Yearly History supplies annual seasonal/permanent water classes from 1984–2021.

The component combines:

- **65% global water-extent stability** — standardized departure of recent global mapped water area from its early-record annual distribution
- **35% permanent-water retention** — recent permanent-water area divided by baseline permanent-water area, capped at 100 so artificial water gains do not create bonus health

## No regional freshwater score

Regional Health files deliberately contain no Freshwater family.

The only public family file is:

```text
frontend/public/data/indices/families/freshwater.index.json
```

and it feeds the top-center Earth Health index.

Large lakes that remain separate MotherWorld selectable regions can still have their own temperature, pollution, biodiversity or species data. They do **not** define the global freshwater aggregation geometry.

## Later hydrological drill-down

If MotherWorld eventually adds basin-level freshwater views, HydroBASINS/GloFAS are the natural hierarchy. That would be a separate product/UI decision, not a change to this global index.


## Earth Health v3

ERA5-Land root-zone soil-moisture stability is the fixed historical Earth Health backbone. GRACE terrestrial water storage and JRC surface-water retention remain current diagnostics with zero Earth Health weight. Freshwater remains global-only.
