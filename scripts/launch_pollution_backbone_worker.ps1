
[CmdletBinding()]
param(
    [string]$RepoRoot = "F:\BiomeSummary",
    [string]$RawRoot = "F:\BiomeSummary\MotherWorld-v8-dataset-downloader\earth_health_backbone_raw",
    [string]$OutputRoot = "F:\BiomeSummary\.cache\motherworld\v8-build\direct-noaa-nasa",
    [string]$WorkRoot = "F:\BiomeSummary\.cache\motherworld\v8-build\direct-noaa-nasa\pollution-work",
    [string]$TokenFile = "F:\BiomeSummary\CpernicusToken\earthdata-token.txt",
    [string]$Python = "F:\BiomeSummary\.cache\climate-venv\Scripts\python.exe",
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"

function Assert-FRoot([string]$Path, [string]$Label) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith("F:\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must be on F: (got $full)"
    }
    return $full
}

$RepoRoot = Assert-FRoot $RepoRoot "repo root"
$RawRoot = Assert-FRoot $RawRoot "raw root"
$OutputRoot = Assert-FRoot $OutputRoot "output root"
$WorkRoot = Assert-FRoot $WorkRoot "work root"
$TokenFile = Assert-FRoot $TokenFile "Earthdata token file"
$Python = Assert-FRoot $Python "Python runtime"
$Builder = Assert-FRoot (Join-Path $RepoRoot "scripts\build_pollution_backbone_direct.py") "NASA builder"
$LogRoot = Assert-FRoot (Join-Path $OutputRoot "logs") "log root"
$TempRoot = Assert-FRoot (Join-Path $OutputRoot "tmp") "temporary root"
$PidFile = Assert-FRoot (Join-Path $LogRoot "pollution-worker.pid") "PID file"
$LaunchManifest = Assert-FRoot (Join-Path $LogRoot "pollution-worker-launch.json") "launch manifest"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Climate Python runtime not found: $Python" }
if (-not (Test-Path -LiteralPath $Builder -PathType Leaf)) { throw "NASA pollution builder not found: $Builder" }
if (-not (Test-Path -LiteralPath $TokenFile -PathType Leaf)) { [Console]::Error.WriteLine("Earthdata token file is missing on F: $TokenFile"); exit 2 }
if ((Get-Item -LiteralPath $TokenFile).Length -le 0) { [Console]::Error.WriteLine("Earthdata token file is empty: $TokenFile"); exit 2 }

New-Item -ItemType Directory -Force -Path $OutputRoot, $WorkRoot, $LogRoot, $TempRoot | Out-Null
$existingBuilders = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*build_pollution_backbone_direct.py*"
})
if ($existingBuilders.Count -gt 0) {
    [Console]::Error.WriteLine("A NASA pollution builder is already running; refusing a duplicate launch.")
    exit 3
}
if (Test-Path -LiteralPath $PidFile) {
    try {
        $stalePid = [int](Get-Content -LiteralPath $PidFile -Raw).Trim()
        $staleProcess = Get-Process -Id $stalePid -ErrorAction Stop
        if ($staleProcess) {
            [Console]::Error.WriteLine("The recorded NASA pollution worker PID $stalePid is still running.")
            exit 3
        }
    } catch {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$StdoutLog = Join-Path $LogRoot "pollution-worker-$stamp.stdout.log"
$StderrLog = Join-Path $LogRoot "pollution-worker-$stamp.stderr.log"
$arguments = @(
    "-u",
    $Builder,
    "--raw-root", $RawRoot,
    "--output-root", $OutputRoot,
    "--work-root", $WorkRoot,
    "--earthdata-token-file", $TokenFile,
    "--start-year", "1993",
    "--end-year", "2025",
    "--min-free-gib", "40",
    "--timeout", "180"
)

$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:TMPDIR = $TempRoot
$manifest = [ordered]@{
    schemaVersion = 1
    status = "launching"
    startedAt = (Get-Date).ToUniversalTime().ToString("o")
    scope = "1993-2025"
    reserveGiB = 40
    worker = "NASA MERRA-2 pollution backbone"
    builder = $Builder
    rawRoot = $RawRoot
    outputRoot = $OutputRoot
    workRoot = $WorkRoot
    stdoutLog = $StdoutLog
    stderrLog = $StderrLog
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LaunchManifest -Encoding UTF8

if ($Foreground) {
    & $Python @arguments 2>&1 | Tee-Object -FilePath $StdoutLog
    $code = $LASTEXITCODE
    $manifest.status = if ($code -eq 0) { "complete" } else { "failed" }
    $manifest.exitCode = $code
    $manifest.finishedAt = (Get-Date).ToUniversalTime().ToString("o")
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LaunchManifest -Encoding UTF8
    exit $code
}

$process = Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog -PassThru
Set-Content -LiteralPath $PidFile -Value ([string]$process.Id) -NoNewline -Encoding ASCII
$manifest.status = "running"
$manifest.pid = $process.Id
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LaunchManifest -Encoding UTF8
Write-Output "Started hidden NASA pollution worker PID=$($process.Id)"
Write-Output "stdout: $StdoutLog"
Write-Output "stderr: $StderrLog"
Write-Output "progress: $(Join-Path $WorkRoot 'progress.jsonl')"
Write-Output "pid: $PidFile"
