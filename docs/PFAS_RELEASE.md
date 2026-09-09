# PFAS release — 2026-09-09

U.S. EPA UCMR5 occurrence data is available lazily in the Contaminants tab for 88 land ecoregions. The existing 190 marine contaminant payloads are preserved byte for byte. This is U.S. drinking-water coverage, not a global PFAS inventory.

The local source snapshot contains 1,992,002 rows across all contaminants. The builder selects the 29 PFAS analytes and excludes lithium. Collection dates in the actual source include 2023–2026; the monitoring-program period is separately labelled 2023–2025. The old hardcoded collection-period label was corrected without dropping valid 2026 observations.

Concentrations are converted from micrograms per litre to nanograms per litre using a factor of 1000. Non-detects contribute to result counts and detection-rate denominators but never receive substituted concentration values. Displayed counts mean analyte-result rows and distinct water-system IDs. A physical water sample can produce multiple analyte results; these counts must not be interpreted as distinct water samples or exact monitoring-station counts.

Water-system service ZIPs are matched to representative points within Census 2020 ZIP Code Tabulation Areas (ZCTAs), then to the canonical regional footprints. This is a service-area exposure proxy, not the location of a sampling point or source-water body. A system may serve more than one ecoregion, so regional totals must not be summed into a national total. Coverage resolves 18,965 of 20,768 service ZIPs and maps 10,229 of 10,318 water systems. Unresolved coverage remains missing.

Source: [EPA occurrence data](https://www.epa.gov/dwucmr/occurrence-data-unregulated-contaminant-monitoring-rule). Source-file and regional-payload hashes are in [the release receipt](PFAS_RELEASE_20260909.json).

Validation: source fixture verifies lithium filtering, exact unit conversion, non-detect preservation and dates through 2026. All 88 regional payloads pass finite JSON, analyte, units and count checks. The interface now supports land-region contaminants and labels result/system counts accurately; the previous marine-only load guard was removed.

Rebuild with `scripts/build_ucmr5_pfas_v8.py --repo <repo> --ucmr5 <EPA directory> --zcta <Census GeoJSON> --output-root <isolated stage>`. The source period derives from observed dates. Review the isolated outputs before merging: preserve existing providers, remove private acquisition paths from public source metadata, and retain full private provenance in staging.
