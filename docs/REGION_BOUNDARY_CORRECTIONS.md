# Seven-region boundary issue and correction

Last audited: **9 September 2026**. The original comparisons use `fc565d78`; shared source fixes are in `0b64991b`, `e1ee7442`, and `7d94246b`. The geology replacement is recorded in [the September 9 release receipt](GEOLOGY_RELEASE_20260909.json).

**Status: species is fixed and published. Shared boundary corrections, producer provenance, and stale-result guards are implemented across the audited root pipelines:** geology/biodiversity (`0b64991b`), climate/pollution (`e1ee7442`), and local regional diagnostics (`7d94246b`). **The regional diagnostics release includes rebuilt PHYLACINE records for all seven regions, with verified geometry provenance and explicit no-native-cell results. The geology release includes all seven corrected GLiM summaries and verified whole-ocean provider identities. Affected temperature, climate-extras, and pollution data releases remain pending.** Source implementation is complete for the audited paths; remaining components still require rebuild/release checks.

## What happened

Seven species inventories initially had no records because their queries used unsuitable region footprints. Map simplification and small-polygon filtering removed many disconnected parts. The two Brazilian island regions also had incorrect locations in the original source geometry. Loosening occurrence-quality filters could not recover observations outside the queried footprint.

The species repair uses the tracked [seven corrected footprints](../scripts/data/species-query-geometries.geojson), validated by [species_query_geometry.py](../scripts/species_query_geometry.py) and applied by [gbif_build_region_species.py](../scripts/gbif_build_region_species.py). Five footprints preserve the full repaired RESOLVE/Ecoregions2017 multipart geometry. Two use attributed official Brazilian island-group baseline envelopes. Species inventories were rebuilt and published in commit `59422766`, retaining the occurrence-quality filters.

Other builders have their own geometry loaders. The shared [analysis geometry helper](../scripts/analysis_geometry.py) now applies the maintained registry in geology, biodiversity, and the reviewed root climate/pollution families. It records the footprint used by each calculation; their mergers and cache readers withhold affected data when producer identity is absent, mismatched, or cannot be verified against the current reference. The [regional-diagnostics adapter](../scripts/regional_diagnostics_adapters.py) now uses the same corrected footprints and producer-identity checks, preserving original-shapefile-first loading. Correcting species queries, unifying canonical region IDs, or fixing JSON serialization alone does **not** update other analysis footprints.

## Affected regions

Areas below are approximate EPSG:6933 equal-area measurements. The corrected area uses the maintained registry, including the official replacements for the two Brazilian island groups.

| Region ID | Territory | Display LOD0 area, km² | Corrected analysis area, km² | Required correction |
| --- | --- | ---: | ---: | --- |
| `eco_117` | Adélie Land tundra | 13.502 | 177.625 | Restore disconnected source polygons |
| `eco_121` | Ellsworth Land tundra | 7.027 | 219.624 | Restore disconnected source polygons |
| `eco_130` | South Antarctic Peninsula tundra | 44.811 | 2,990.181 | Restore disconnected source polygons |
| `eco_267` | South China Sea Islands | 2.537 | 30.537 | Restore the full island group |
| `eco_509` | Trindade–Martin Vaz Islands tropical forests | 5.617 | 15.953 | Apply the official corrected location and island-group footprint |
| `eco_562` | San Félix–San Ambrosio Islands temperate forests | 2.199 | 6.488 | Restore the full island group |
| `eco_609` | St. Peter and St. Paul Rocks | 2.271 | 0.045 | Apply the official corrected location and footprint |

For the five unchanged-source regions, LOD0 retains only approximately 1.1–33.3% of the original footprint. LOD1 also loses relevant parts. For `eco_509` and `eco_609`, the maintained official geometry is disjoint from the old source geometry: merely preferring the original shapefile is insufficient.

## Cross-dataset audit

| Dataset or pipeline | Finding at audit time | Required follow-up |
| --- | --- | --- |
| Recorded species | Corrected footprints applied; all seven inventories rebuilt and published | Preserve the existing correction and quality filters |
| Land geology / GLiM | [geology_common.py](../scripts/geology_common.py) now corrects the base LOD0 geometry. The seven replacement records use the maintained footprints | Rebuilt GLiM records are included in the September 9 geology release; incompatible cached point/line providers remain withheld |
| Land temperature | [temperature_common.py](../scripts/temperature_common.py) now applies the maintained overrides after loading the LOD0 geometry. None of the seven has a rebuilt temperature record | Rebuild only when suitable ERA5 input is available; the existing geometry-hashed `processing_fingerprint` invalidates incompatible reductions |
| Regional precipitation, humidity, and wind | [climate_extras_common.py](../scripts/climate_extras_common.py) now applies the maintained overrides after loading LOD0. None of the seven has a rebuilt regional climate-extras record | Rebuild the regional reduction when inputs are available; output reuse checks the corrected geometry identity |
| Regional biodiversity / PHYLACINE | Original and fallback loaders now apply all seven maintained overrides | Corrected seven-region fragments are included in the regional diagnostics release with verified producer provenance, `no_native_grid_cell`, and null retention; the UI withholds numeric mammal counts |
| Land air pollution, contaminants, and land-pollution diagnostics | Reviewed root loaders now apply corrected geometry for original and fallback land paths; producer merges require matching identity and withhold missing/mismatched claims. No affected seven-region outputs have been rebuilt | Rebuild with valid source coverage; root and staged-output adapters now require geometry and producer provenance |
| Regional environmental indices | These consume component summaries rather than choosing geometry themselves; no seven-region outputs were built | Rebuild after corrected components are available |
| Marine/lake region records | The seven IDs are land regions, so they are not directly selected by marine/lake-only builders | Keep canonical IDs and normal provider coverage checks |
| Fixed global Earth Health backbones and Moon data | Independent of these seven land-region query footprints | No rebuild needed for this specific issue |

**Whole-ocean qualification:** fixed global indicators such as NOAA ocean heat content are independent of these footprints. Geometry-derived whole-ocean geology masks, however, subtract land and freshwater lakes; review that exclusion mask when propagating land-boundary corrections. Do not treat every whole-ocean product as automatically independent.

## Measured GLiM effect

The audit reproduced the current public GLiM results from the staged provider source, then recomputed them with the maintained corrected footprints. The corrected values below are now included in the September 9 geology release; the older values remain here as the audit baseline.

| Region | Previous published result at `fc565d78` | Corrected-footprint comparison |
| --- | --- | --- |
| Adélie Land (`eco_117`) | 0% classified rock; all source no-data | 56.425% classified rock, including about 100.226 km² of metamorphic rocks |
| Ellsworth Land (`eco_121`) | 100% basic volcanic rocks within the small retained footprint | 69.518% classified rock across the full footprint, with basic volcanic, metamorphic, intermediate volcanic, and mixed sedimentary classes |
| South Antarctic Peninsula (`eco_130`) | 55.100% classified rock; metamorphic class only | 54.126% classified rock over a much larger area, with metamorphic, siliciclastic sedimentary, and intermediate volcanic classes |
| The other four regions | Source no-data across the retained footprint | Corrected location/area, but GLiM still has no mapped rock data there |

Fixing the boundary cannot create coverage that the provider does not have.

## Raster coverage is a separate issue

The inspected PHYLACINE grid is native EPSG:6933, 360 × 142 cells. Both the staged and corrected footprints for all seven regions contain **zero native cell centers**. Their status remains `no_native_grid_cell`; retention metrics are null. Numeric count fields in such a payload must not be presented as evidence of zero mammals or complete faunal loss.

The regional diagnostics release includes all 847 land records: 707 with native grid coverage and 140 with no native cell. The seven corrected payloads have matching registry fingerprints and producer cache identities. The lazy Biodiversity panel gates numeric output on native coverage before formatting any counts.

An independent PHYLACINE rasterization bug was also fixed in source `a5977a1c`: rasterizing all regions with `all_touched=True` let later shapes overwrite earlier regions' cells. The corrected cell-center build reported 707 covered land regions and 140 with no native cell. That fix is separate from the seven-region boundary correction.

No land-temperature value comparison was performed: the inspected cache had marine OISST data, not suitable land ERA5 input for these seven regions. Missing temperature/pollution records and insufficient raster resolution must remain explicit availability states. Missing observations do not mean clean land, zero species, or zero environmental pressure.

## Propagation and acceptance checklist

- [x] Maintain the seven attributed corrected footprints in one tracked registry.
- [x] Apply them to species queries, preserve quality filters, rebuild, and publish the inventories.
- [x] Audit other regional geometry loaders and demonstrate the GLiM numerical impact.
- [x] Add a shared analysis-geometry step that applies the maintained overrides after loading/repairing/unioning base land geometry. Preserve canonical IDs, WGS84 geometry, and source attribution (`0b64991b`, `e1ee7442`).
- [x] Use that step in all audited regional builders, including original and fallback geometry paths and the local regional-diagnostics adapter (`0b64991b`, `e1ee7442`, `7d94246b`).
- [ ] Replace or validate affected legacy cached results. Current root cache readers and mergers require the corrected identity; temperature retains its geometry-hashed `processing_fingerprint`. Legacy OSM count partials still need targeted recalculation. Reuse raw downloads only when their spatial/temporal coverage satisfies the corrected request.
- [x] Rebuild all seven GLiM summaries and validate geometry-derived whole-ocean exclusion masks. Other affected regional climate/pollution components remain pending.
- [x] Preserve explicit GLiM no-data and PHYLACINE no-native-cell results where corrected geometry still lacks provider coverage. Apply the same rule to remaining providers.
- [x] Add regression checks for all seven overrides, the two official location replacements, source priority, missing reference geometry, and output/provenance consistency. Source fixing commits are recorded above; data-release status remains separate.

The source correction in `0b64991b` passed 13 focused analysis-geometry, geology, and existing species-geometry tests, including rejection when a required reference footprint is unavailable. The `e1ee7442` climate/pollution changes passed 24 combined focused checks; the `7d94246b` adapter passed 7 checks. Late stale updates cannot erase a current verified result, and missing or uncorrected reference geometry cannot bypass validation. No affected seven-region climate/pollution outputs have been rebuilt. The existing OSM processing job saves regional counts rather than individual sites; those counts cannot be assigned corrected provenance without a targeted recalculation. Its affected records remain pending.

The corrected registry belongs to offline data analysis. Its propagation must not reintroduce large detailed boundary assets into the browser or undo the map performance work.

The direct regional CDS acquisition script added in `83b8d26b` also applies the maintained seven land footprints before constructing request bounds and cache fingerprints. Its validated pilot is an acquisition check, not a completed seven-region climate release.

## Additional geology release check: stale ComCat output

On 9 September 2026, review rejected the expanded geology candidate before publication. The corrected seven GLiM footprints, all eight geometry-derived whole-ocean provider identities, and GEBCO depth distributions passed. A separate output-reuse bug remained: a filtered earthquake rebuild left older provider files behind for regions with no matching earthquakes. Geometry guards for the seven corrected regions do not detect stale event content in other regions.

The validated raw catalog contains 57,541 events; its earthquake-only derivative contains 57,468 earthquakes and excludes 73 other events. The candidate nevertheless retained non-earthquake events. It also contained two nested public trees: the selected outer index reported 469 ComCat regions while its manifest described 604 from the nested tree. Neither tree was accepted or published.

Acceptance requires a clean isolated ComCat output directory, an event-level check against the earthquake-only derivative, explicit filter provenance on every emitted fragment, and one direct public tree whose index, manifest counts, and archive agree. Empty-result regions must not inherit old provider output. The September 9 replacement was rebuilt in an empty provider directory and verified against the filtered catalog. It contains 601 ComCat summaries, 1,098 regions overall, and exactly 1,099 public JSON files in one direct tree. The reusable builder now enforces the normalized earthquake type filter, records filter counts, validates and serializes new output before replacing older fragments, and removes stale fragments even when a valid rebuild has no matching earthquakes. See [the release receipt](GEOLOGY_RELEASE_20260909.json) and [regression tests](../tests/test_usgs_seismicity.py). Local audit evidence is retained in `.cache/motherworld/v8-handoffs/geology-boundary-candidate-validation-20260909.md`.

## Maintainer references

- [Corrected footprints and attribution](../scripts/data/species-query-geometries.geojson)
- [Existing species geometry regression tests](../tests/test_species_query_geometry.py)
- [Species pipeline](../SPECIES_PIPELINE.md)
- [Temperature pipeline](../CLIMATE_PIPELINE.md)
- [Architecture](../ARCHITECTURE.md)

Local audit evidence is retained under `.cache/motherworld/boundary-carryover-audit/` (`geology-biodiversity.md/.json` and `climate-pollution.md/.json`). The earlier diagnosis is under `.cache/motherworld/empty-region-diagnostics/`. Those working files and large raw datasets are not required to read this tracked issue record; the affected IDs, verified comparisons, correction source, and outstanding steps are preserved here for future clones.
