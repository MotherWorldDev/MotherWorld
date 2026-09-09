# Geology tab

Geology is **descriptive regional context**. It does not contribute to Earth Health.

The tab is deliberately broad: surface lithology, geologic age / mapped units, known mineral occurrences, active faults and long-term seismicity, volcanism, geothermal heat flow, confirmed impact structures, and — for marine regions — seafloor geology/geophysics.

## Public schema

```text
frontend/public/data/geology/
  geology.index.json
  land/<region>.json
  marine/<region>.json
  lakes/<region>.json
```

The September 9 release adds GEBCO bathymetry and earthquake-only USGS ComCat summaries, with all seven maintained land-boundary corrections and the whole-ocean mask verified. See the [release receipt](GEOLOGY_RELEASE_20260909.json) and [boundary issue history](REGION_BOUNDARY_CORRECTIONS.md).

Raw provider files stay under `geology_raw/` or provider caches and are not deployed. Provider fragments are written under `.cache/motherworld/geology/providers/` and merged by `scripts/merge_geology.py`.

## Marine / seafloor section

Marine regions can expose:

- GEBCO bathymetry: mean/median depth, P10/P90, deepest sampled depth, and area shares by shelf/slope/bathyal/abyssal/hadal depth zone;
- present-day oceanic crust age from Seton et al. / EarthByte, plus optional spreading-rate statistics;
- GlobSed total sediment thickness;
- optional CRUST1.0 crystalline crust thickness;
- mapped spreading-ridge and trench/subduction-line length;
- InterRidge hydrothermal vent fields;
- submarine volcanoes and offshore heat-flow/seismic/impact context from the existing Geology providers.

`open_ocean` follows the MotherWorld scope rule: its selectable geometry remains the visual residual, but **all non-species Geology metrics for that selection are whole-ocean aggregates**. The seafloor builders therefore replace the residual analysis geometry with the union of the residual + all mapped marine/coastal ecoregions.

The gridded seafloor builders deliberately summarize native high-resolution rasters on a coarser statistics grid (0.1° default) to make complete-world preprocessing practical. Native source resolution and statistics resolution are both recorded in the payload.

## Build

Stage whichever providers you want under `geology_raw/`, then run:

```bash
python scripts/build_all_geology.py
```

Macrostrat is network-backed and opt-in because hundreds of regional requests can be slow:

```bash
python scripts/build_all_geology.py --macrostrat
```

For development, target individual regions for raster summaries:

```bash
python scripts/build_all_geology.py --region marine_meow_20192
```

## Interpretation rules

- GLiM percentages describe mapped surface lithology, not subsurface volume.
- MRDS values are known occurrences/deposits, **not reserves or economic value**.
- GEM faults are mapped active faults and coverage is not spatially uniform.
- GVP eruption counts are confirmed catalog records, not a complete proxy for hazard.
- Heat flow is measured/interpolated geothermal state, not guaranteed power-generation potential.
- Only confirmed impact structures belong in the default impact view; source-supplied age uncertainty and impactor estimates are preserved, never invented.
- Earthquake history is a catalog of recorded events; it is not a deterministic hazard forecast.
- GEBCO is an information product assembled from source data of varying resolution/quality and is **not for navigation**.
- Seafloor-age coverage describes where the oceanic-crust age model has values; missing age is not automatically equivalent to continental crust.
- Hydrothermal-vent counts are strongly influenced by exploration effort. No record does not mean no venting.
- Ridge/trench line length depends on the staged tectonic-feature compilation and its representation scale.

## Obtaining the provider files

See `docs/GEOLOGY_DOWNLOADS.md` for official source pages, staging names and the automated filename patterns used by `build_all_geology.py`.
