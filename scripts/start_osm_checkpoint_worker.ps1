[CmdletBinding()]
param(
    [string]$Repo = 'F:\BiomeSummary',
    [ValidateRange(1, 1024)][int]$CheckpointBlocks = 8,
    [switch]$VerifyInput
)
$ErrorActionPreference = 'Stop'
$taskRepo = (Resolve-Path -LiteralPath $Repo).Path
if ([System.IO.Path]::GetPathRoot($taskRepo) -ne 'F:\') { throw 'OSM workspace must stay on F:.' }
$taskRunner = Join-Path $taskRepo 'scripts\build_osm_waste_sites_stream.py'
$taskPython = Join-Path $taskRepo '.cache\climate-venv\Scripts\python.exe'
foreach ($taskRequired in @($taskRunner, $taskPython)) {
    if (!(Test-Path -LiteralPath $taskRequired -PathType Leaf)) { throw "Missing runtime file: $taskRequired" }
}
$taskExisting = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'build_osm_waste_sites(?:_guarded|_stream)?\.py' })
if ($taskExisting.Count) { throw 'An OSM worker is already running. Refusing to start a duplicate.' }
$taskStage = Join-Path $taskRepo '.cache\osm-recovery'
$taskTemp = Join-Path $taskRepo '.cache\tmp'
New-Item -ItemType Directory -Force -Path $taskStage, $taskTemp | Out-Null
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
$env:TMPDIR = $taskTemp
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONPATH = "$taskRepo\scripts;$taskRepo\.cache\osm-runtime;$taskRepo\.cache\python"
$taskInputs = @('europe-260907.osm.pbf','africa-260907.osm.pbf','antarctica-260907.osm.pbf','asia-260906.osm.pbf','australia-oceania-260907.osm.pbf','central-america-260907.osm.pbf','north-america-latest.osm.pbf','south-america-260907.osm.pbf')
$taskArguments = @('-u', $taskRunner, '--repo', $taskRepo, '--output-root', (Join-Path $taskRepo '.cache\motherworld\v8-build\local-diagnostics'), '--checkpoint-blocks', [string]$CheckpointBlocks)
if ($VerifyInput) { $taskArguments += '--verify-input' }
foreach ($taskName in $taskInputs) {
    $taskInput = Join-Path $taskRepo ('MotherWorld-v8-dataset-downloader\pollution_raw\osm\' + $taskName)
    if (!(Test-Path -LiteralPath $taskInput -PathType Leaf)) { throw "Missing input: $taskName" }
    $taskArguments += @('--input', $taskInput)
}
# Start-Process joins ArgumentList; quote each path/argument explicitly for Windows.
$taskQuotedArguments = $taskArguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$taskOut = Join-Path $taskStage "$taskStamp.checkpoint.stdout.log"
$taskErr = Join-Path $taskStage "$taskStamp.checkpoint.stderr.log"
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList $taskQuotedArguments -WorkingDirectory $taskRepo -WindowStyle Hidden -RedirectStandardOutput $taskOut -RedirectStandardError $taskErr -PassThru
$taskReceipt = @{startedAt=(Get-Date -Format o); wrapperPid=$taskProcess.Id; stdout=$taskOut; stderr=$taskErr; status='starting'; checkpointBlocks=$CheckpointBlocks; runner=$taskRunner; reserveGiB=40; temp=$taskTemp; pythonPath=$env:PYTHONPATH}
$taskReceipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskStage 'checkpoint-launch.json') -Encoding UTF8
$taskReceipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskStage 'launch.json') -Encoding UTF8
$taskReceipt | ConvertTo-Json