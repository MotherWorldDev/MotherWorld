# Direct historical backbone runbook

The canonical historical-provider path for this stack is direct access to the
official source products. The Earth Engine builders remain optional legacy
adapters; they are not required for the direct run.

All raw inputs and build checkpoints stay on F:, outside the public deployment
tree. The following workspace directories are local staging paths:

- raw staging: `F:\\BiomeSummary\\MotherWorld-v8-dataset-downloader\\earth_health_backbone_raw`
- direct outputs/checkpoints: `F:\\BiomeSummary\\.cache\\motherworld\\v8-build\\direct-noaa-nasa`

The builders never publish data. The root integration task is responsible for
copying a verified family index into `frontend/public/data`.

## NOAA AVHRR/VIIRS vegetation

Source: [NOAA NCEI Land-NDVI CDR direct access](https://www.ncei.noaa.gov/data/land-normalized-difference-vegetation-index/access/).
The builder uses NOAA AVHRR NDVI CDR v5 through 2013 and NOAA VIIRS NDVI
CDR v1 from 2014 onward. It reads the native daily `NDVI` and `QA` variables,
applies the sensor-specific CDR quality flags, reduces each day to an
approximately 0.4° working grid (`--coarsen 8`), and takes the annual median.
The fixed baseline is the per-pixel median of 1982–1992 annual medians. The
scored series starts in 1993 and uses annual NDVI divided by that baseline,
clipped to 0–1 and area-weighted over pixels whose baseline exceeds 0.10.
No alternate NDVI product or date sampling is substituted.

### Full-run workload estimate

The estimate below pins the right edge to 2025, which is the current builder
default on 2026-09-08. It comes from a read-only inventory of NOAA's official
public S3 mirror on that date, using the exact object sizes rather than a
single-file average. The NCEI HTTPS listing used by the builder exposes the
same CDR filenames; two staged samples were also byte-for-byte consistent with
the S3 objects.

| Period | Sensor | Daily files | Estimated transfer |
|---|---|---:|---:|
| 1982–1992 fixed baseline | AVHRR v5 | 4,002 | 247.149 GB / 230.176 GiB |
| 1993–2013 scored history | AVHRR v5 | 7,668 | 476.175 GB / 443.473 GiB |
| 2014–2025 scored history | VIIRS v1 | 4,366 | 282.530 GB / 263.126 GiB |
| **1982–2025 total** | **AVHRR + VIIRS** | **16,036** | **1,005.854 GB / 936.775 GiB** |

The inventory has 35 missing calendar-day objects relative to the theoretical
16,071 days: 16 in the baseline, 2 in scored AVHRR years, and 17 in scored
VIIRS years. The builder does not synthesize them; it records only years with
at least one source file and computes each annual median from the available
valid daily observations. The per-year counts are retained in the build
metadata so incomplete years remain visible.

The streaming worker downloads and quality-filters daily files into a disk-backed
annual working array. It saves the annual reduction, grid, and a source manifest
with source URLs, sizes, and SHA-256 hashes before deleting worker-owned raw
files. Pre-existing source files are preserved. Resuming validates the annual
checkpoint, grid, configuration, and recorded source set before reuse.

The full 1982–2025 run started on 2026-09-09. Its default limits are a 32 GiB
worker-owned raw cache, a 128 MiB per-file cap, and a 40 GiB free-space reserve.
The transfer estimate above is cumulative traffic, not required simultaneous
disk space. Runtime depends on source throughput and annual reduction overhead.

For a fresh run, or after confirming the previous worker has stopped:

```powershell
python scripts/run_vegetation_backbone_worker.py `
  --raw-root F:\BiomeSummary\MotherWorld-v8-dataset-downloader\earth_health_backbone_raw `
  --output-root F:\BiomeSummary\.cache\motherworld\v8-build\direct-noaa-nasa `
  --end-year 2025 --coarsen 8
```

The output root contains `vegetation-worker.log`,
`vegetation-worker-status.json`, and the active worker PID file. The wrapper
records completion or failure; it does not automatically restart failed runs.
Do not launch a second worker against the same working directory.
Use `--skip-download` only when the required daily files are already staged.
Use `--retain-raw` only when the extra raw storage is intentional.

### Official subset and mirror audit

NOAA also publishes the same CDR through an official THREDDS “best time
series” aggregation with OPeNDAP and NCSS services:
`https://www.ncei.noaa.gov/thredds/ncss/grid/ncFC/cdr/ndvi-fc/AVHRR_and_VIIRS_NDVI%3A_aggregation_best.ncd/dataset.html`.
Its documented axes are still daily 0.05° NDVI/QA data, not a monthly or
annual composite. NCSS supports a geographic box and horizontal stride, so a
request with `horizStride=8` could reduce transfer while retaining every
date, but spatial subsampling would differ from the current quality-filtered,
area-weighted block means and must not be substituted without method review. During this run, bounded probes
to both the aggregation NCSS route and per-file NCSS/OPeNDAP routes did not
return within 45 seconds, so this route is recorded as an optimization
candidate, not silently substituted for the validated HTTPS path.

NOAA’s official public S3 mirror (`s3://noaa-cdr-ndvi-pds`) was also checked.
The `data/YYYY/` prefixes contain daily NetCDF objects (365 objects in the
1993 and 2025 listings) and no same-product monthly/annual branch. S3 can
improve transport or resume behavior, but it does not reduce the CDR byte
volume or change the builder’s daily-median semantics. The [NOAA CDR landing
page](https://www.ncei.noaa.gov/products/climate-data-records/normalized-difference-vegetation-index)
links the direct, THREDDS, and AWS access paths.

## NASA MERRA-2 pollution

Source: [NASA MERRA-2 aerosol product](https://disc.gsfc.nasa.gov/datasets/M2TMNXAER_5.12.4/summary).
The builder resolves each monthly granule through public CMR metadata and asks
GES DISC Cloud OPeNDAP for only `DUSMASS25`, `OCSMASS`, `BCSMASS`, `SSSMASS25`,
`SO4SMASS`, coordinates, and time. It reconstructs surface PM2.5 as

`DUSMASS25 + OCSMASS + BCSMASS + SSSMASS25 + SO4SMASS × (132.14 / 96.06)`,

then converts kg/m³ to µg/m³ and weights by the official `M2C0NXASM`
`FRLAND` fraction and cell areas derived from native latitude/longitude edges.
The constant product does **not** contain an `AREA` variable: requesting it
causes an HTTP 400 response. Areas use the spherical latitude-band formula
`R² × (sin(lat_hi) − sin(lat_lo)) × Δlongitude`. The actual native grid spans
−90° to 90° latitude and −180° to 179.375° longitude at 0.5° × 0.625°.
The builder computes calendar-day-weighted annual means over latitudes
−60° to 85°. The healthy/critical normalization remains 5/50 µg/m³.

The current GES DISC endpoint redirects anonymous requests to NASA Earthdata
Login. A real build therefore requires a NASA Earthdata user token. Create or
sign in to an account, authorize GES DISC, and generate a token using the
[Earthdata token instructions](https://urs.earthdata.nasa.gov/documentation/for_users/user_token).
Keep the token on F: and pass it as either `EARTHDATA_TOKEN` or
`--earthdata-token-file`:

```powershell
python scripts/build_pollution_backbone_direct.py `
  --raw-root F:\\BiomeSummary\\MotherWorld-v8-dataset-downloader\\earth_health_backbone_raw `
  --output-root F:\\BiomeSummary\\.cache\\motherworld\\v8-build\\direct-noaa-nasa `
  --earthdata-token-file F:\\BiomeSummary\\secrets\\earthdata.token
```

Without a token the builder performs only the anonymous access probe, writes
`direct-access-report.json` under the output root, and exits with the exact
user action required. It does not fabricate a pollution series or download a
full monthly file.

### Validated launch and resume

On 2026-09-09, authenticated downloads and a complete twelve-month 1993 pilot
passed validation. The full 1993–2025 worker was then launched using
`scripts/launch_pollution_backbone_worker.ps1`. It uses an ignored local token
file, enforces a 40 GiB free-space reserve, and refuses duplicate active builders.
Credentials are never embedded in published family output.

Under the configured output root, `logs/pollution-worker-launch.json` records
the launch PID and log paths; `pollution-work/progress.jsonl` records actual
month/year progress. A launch manifest saying `running` is not proof of a live
process: check its PID, progress, and stderr before a restart. Resume validates
source request identities, native grid, units, requested month, file content,
processing methods, normalization thresholds, and complete annual month records.
Do not publish a pilot or a partial timeline as the complete historical family.

## Verification and handoff

Focused tests are in `tests/test_direct_backbones.py`,
`tests/test_vegetation_streaming.py`, and
`tests/test_pollution_backbone_hardening.py`. Before root
integration, run them with the repository’s F:-hosted test dependencies and
inspect the generated family JSON for its source, method, latest year, and
coverage fields. Keep the NOAA workload estimate and the NASA access report
with the build handoff so a later run is auditable.
