# Data sources

## UNEP GEMS/Water GEMStat Global Freshwater Quality Archive

Primary broad freshwater contaminant source.

- Current open archive version used by this package: v3, published February 2026
- DOI: `10.5281/zenodo.18459694`
- Open subset contains over 50 million measurements on 622 parameters, nearly 23,000 stations in 42 countries, spanning 1906–2024.
- The open archive corresponds to GEMStat data published under CC BY 4.0 or equivalent open terms.

GEMStat includes a wide variety of chemical and biological parameters. Publicly documented examples include mercury, PFOS, glyphosate, atrazine, DDT-family pesticides, petroleum hydrocarbons, metals, PCBs/PBDEs and microbial indicators such as E. coli/fecal coliforms.

Coverage is highly uneven geographically and temporally.

## NOAA NCEI Marine Microplastics Database

A global database of marine microplastic observations from 1972 to present.

Records include concentration, coordinates, date, marine setting, sampling method and source publication. Concentration units differ by setting and study; NOAA explicitly cautions that methods are not standardized enough for all measurements to be directly comparable.

Source: `https://www.ncei.noaa.gov/products/microplastics`

## U.S. EPA UCMR 5

UCMR5 monitored 29 PFAS and lithium in U.S. public drinking-water systems during 2023–2025. EPA released the final dataset in August 2026.

MotherWorld uses only the PFAS analytes.

Source: `https://www.epa.gov/dwucmr/occurrence-data-unregulated-contaminant-monitoring-rule`

Spatial caveat: the bulk occurrence data provides public-water-system identifiers and served ZIP codes, not exact sample coordinates. The pack therefore maps systems through service-ZIP representative points and labels this as an approximate exposure/service-area overlay.

## NOAA IncidentNews

NOAA Office of Response and Restoration publishes a machine-readable `incidents.csv` covering selected incidents where OR&R provided scientific support, including oil and chemical threats.

Source: `https://incidentnews.noaa.gov/raw/incidents.csv`

The archive is public domain but is not a complete global oil-spill database.

## European CleanSeaNet (not automated in v1)

EMSA publishes annual CleanSeaNet detections/feedback data for Europe from 2015 onward. These detections can indicate mineral oil or other phenomena and require validation by member states.

This pack does not automatically ingest CleanSeaNet because the annual publication bundles/report schemas are not stable enough to treat as one unattended global feed. The common output schema can accept a future EMSA importer.
