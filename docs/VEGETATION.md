# Vegetation family

Primary provider: NASA MODIS/Terra Vegetation Continuous Fields v6.1 (`MODIS/061/MOD44B`).

The provider builder generates annual ecoregion means for tree cover, non-tree vegetation, non-vegetated cover, total vegetation cover, baseline cover, and long-term trend. The family score is **condition relative to the ecoregion's own early-series baseline**, not absolute greenness, so deserts and tundra are not penalized for naturally sparse vegetation.

Default score inputs:

- vegetation-cover retention vs baseline — 55%
- tree-cover retention vs baseline — 25%
- long-term vegetation trend — 20%

All weights and normalizations are code-level policy choices and can be moved to configuration later without changing provider JSON.

## Earth Health historical backbone (v3)

The regional/current Vegetation module above remains a present-day diagnostic. It does **not** determine the Earth Health headline. Earth Health uses the NOAA AVHRR/VIIRS NDVI Climate Data Record as its fixed annual vegetation backbone so the same vegetation method can be evaluated from 1993 onward. MODIS VCF remains visible in the expanded current Vegetation card with zero hidden Earth Health weight.
