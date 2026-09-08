# Contaminant observations pipeline

## Design rule

This layer is fundamentally different from satellite water quality.

Remote sensing can estimate broad optical/ecological water condition continuously over space. PFAS, mercury, pesticides, pathogens and many other toxic contaminants require in-situ sampling or incident records.

Therefore every MotherWorld contaminant result carries its monitoring footprint.

## Spatial matching

### GEMStat

Freshwater monitoring stations are point-in-polygon joined to:

1. terrestrial MotherWorld ecoregions; and
2. mapped MotherWorld lake polygons when the station lies inside one.

This lets a terrestrial region answer “what freshwater contaminants have actually been measured inside this ecoregion?” while a mapped lake gets its own observation record.

### NOAA microplastics / IncidentNews

Marine point records are matched to MotherWorld marine ecoregions.

### EPA UCMR5

Exact sampling coordinates are not available in the bulk public occurrence files. Public-water-system served ZIP codes are mapped by representative point to terrestrial ecoregions. Results can be associated with more than one region when a system serves multiple ZIP codes.

This is intentionally labelled as approximate.

## Censored / non-detect values

When a source includes a `<`, non-detect or below-reporting-limit qualifier:

- the record contributes to `sampleCount`;
- it does not contribute to detected concentration quantiles;
- it contributes to the denominator of `detectionRatePct`.

The pipeline does not substitute half the detection limit or another arbitrary value.

## Units

Compatible mass-per-volume units are normalized:

- PFAS → ng/L
- mercury/pesticides/petroleum/other selected toxics → µg/L

Pathogen counts and microplastic measurements remain in reported units. Incompatible units are separate analyte series.

## Quantiles

Counts are exact. To avoid unbounded memory on very heavily sampled analytes, detected values use deterministic reservoir sampling after the configured maximum number of retained values (default 10,000 per region/analyte/unit series). The JSON says when quantiles were reservoir-sampled.

## Interpretation

Do not infer:

- no contamination from no observations;
- population exposure from an environmental station concentration;
- regulatory compliance from MotherWorld summaries;
- toxicity from concentration alone without compound-specific thresholds and exposure pathways.
