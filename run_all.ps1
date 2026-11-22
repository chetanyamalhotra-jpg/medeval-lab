<#
.SYNOPSIS
    One-command reproduction of every medeval-lab result.

.DESCRIPTION
    Creates (or reuses) a virtual environment, installs pinned dependencies,
    registers the read-only lm-evaluation-harness checkout, runs the test suite,
    and then runs the full pipeline: item validation, literature traceability,
    benchmark audit, harness baseline and Experiment 001.

    The script is idempotent and re-runnable. Nothing outside this repository is
    modified, except the pip installation of this package into .venv and (if a
    pip editable install of the harness is impossible) a .pth file that points at
    the harness checkout.

.PARAMETER Limit
    Items per task for the harness baseline (default 20).

.PARAMETER SkipInstall
    Reuse the existing .venv without installing anything.

.PARAMETER SkipTests
    Skip the pytest run.

.PARAMETER DryRun
    Run the Experiment 002 preflight only; it never contacts a paid model API.

.PARAMETER HarnessPath
    Path to the lm-evaluation-harness checkout (default: MEDEVAL_HARNESS_PATH or
    a sibling ``lm-evaluation-harness`` directory next to this repository).
#>
[CmdletBinding()]
param(
    [int]$Limit = 20,
    [switch]$SkipInstall,
    [switch]$SkipTests,
    [switch]$DryRun,
    [string]$HarnessPath = $(if ($env:MEDEVAL_HARNESS_PATH) { $env:MEDEVAL_HARNESS_PATH } else { '' })
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# The lm-evaluation-harness checkout is optional and is never vendored here
# (upstream is MIT, consumed read-only). Without an explicit path, look for a
# sibling clone next to this repository; if there is none the harness stage is
# skipped and everything else still runs.
if ([string]::IsNullOrWhiteSpace($HarnessPath)) {
    $HarnessPath = Join-Path (Split-Path -Parent $root) 'lm-evaluation-harness'
}

# Keep every temporary artefact inside the repository. The HF datasets cache is
# deliberately a *relative* path: its lock filenames embed the cache path, and an
# absolute path under a deep directory can exceed the Windows MAX_PATH limit.
$env:TEMP = Join-Path $root '.tmp'
$env:TMP = $env:TEMP
$env:MPLCONFIGDIR = Join-Path $root '.tmp\mpl'
$env:HF_DATASETS_CACHE = 'hf'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = Join-Path $root 'packages'
New-Item -ItemType Directory -Force -Path $env:TEMP, $env:MPLCONFIGDIR, (Join-Path $root 'hf') | Out-Null

$python = Join-Path $root '.venv\Scripts\python.exe'

function Write-Step([string]$message) {
    Write-Host ''
    Write-Host "=== $message ===" -ForegroundColor Cyan
}

Write-Step 'medeval-lab run_all.ps1'
Write-Host "repository : $root"
Write-Host "python     : $([System.Environment]::OSVersion.VersionString)"
Write-Host "harness    : $HarnessPath"

if (-not (Test-Path $python)) {
    Write-Step 'Creating virtual environment (.venv)'
    python -m venv "$root\.venv"
    if (-not (Test-Path $python)) {
        Write-Host 'venv creation with pip failed; retrying with --without-pip' -ForegroundColor Yellow
        python -m venv --without-pip "$root\.venv"
    }
}

if (-not (Test-Path $python)) {
    Write-Host 'Could not create a virtual environment. Install Python 3.11+ and re-run.' -ForegroundColor Red
    exit 1
}

if (-not $SkipInstall) {
    Write-Step 'Installing dependencies'
    & $python -m pip install --disable-pip-version-check --no-input -r (Join-Path $root 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Write-Host 'dependency install failed' -ForegroundColor Red; exit 1 }
    & $python -m pip install --disable-pip-version-check --no-input --no-build-isolation -e $root
    if ($LASTEXITCODE -ne 0) { Write-Host 'editable install of medeval-lab failed' -ForegroundColor Red; exit 1 }

    Write-Step 'Registering lm-evaluation-harness (read-only checkout)'
    if (-not (Test-Path $HarnessPath)) {
        Write-Host "harness checkout not found at $HarnessPath" -ForegroundColor Yellow
        Write-Host 'The harness baseline stage will be skipped; everything else still runs.' -ForegroundColor Yellow
    }
    else {
        & $python -m pip install --disable-pip-version-check --no-input --no-deps --no-build-isolation -e $HarnessPath
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'pip editable install of the harness failed; linking the checkout with a .pth file instead' -ForegroundColor Yellow
            $sitePackages = & $python -c "import site; print(site.getsitepackages()[0])"
            Set-Content -Path (Join-Path $sitePackages 'lm_eval_harness.pth') -Value $HarnessPath -Encoding ascii
        }
        & $python -c "import lm_eval; print('lm_eval', lm_eval.__version__)"
    }
}

if (-not $SkipTests) {
    Write-Step 'Running test suite'
    & $python -m pytest tests -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { Write-Host 'tests failed' -ForegroundColor Red; exit 1 }
}

if ($DryRun) {
    Write-Step 'Dry run: skipping the full pipeline'
    Write-Host 'No command that could use a configured model endpoint will run.' -ForegroundColor Yellow
    $pipelineExit = 0
}
else {
    Write-Step 'Running full pipeline (Experiment 001 is forced MOCK)'
    & $python -m medeval all --limit $Limit
    $pipelineExit = $LASTEXITCODE
}

Write-Step 'Experiment 002 dry run (mock judges, no network calls)'
& $python -m medeval run exp002 --dry-run --n-items 8
if ($LASTEXITCODE -ne 0) { $pipelineExit = $LASTEXITCODE }

Write-Step 'Experiment 003 dry run (mock judges and screener, no network calls)'
& $python -m medeval run exp003 --dry-run --n-items 140 --screening-size 60
if ($LASTEXITCODE -ne 0) { $pipelineExit = $LASTEXITCODE }

Write-Step 'Paper Packs: validate every pack (offline)'
& $python -m medeval paper validate
if ($LASTEXITCODE -ne 0) { $pipelineExit = $LASTEXITCODE }

Write-Step 'Paper Packs: matrix dry runs (mock targets/judges, no network calls)'
foreach ($pack in @(
    'template-clinical-mcq',
    'template-rubric-panel',
    'template-structured-extraction',
    'healthbench-rubric',
    'medcalc-bench',
    'medhallu-detect',
    'medxpertqa-text',
    'medsafety-bench'
)) {
    & $python -m medeval matrix run --paper $pack --dry-run --n-items 4 --max-calls 80
    if ($LASTEXITCODE -ne 0) { $pipelineExit = $LASTEXITCODE }
}

Write-Step 'Archived Experiment 002a preflight (no API calls)'
& $python -m medeval exp002a --dry-run
if ($LASTEXITCODE -ne 0) { $pipelineExit = $LASTEXITCODE }

Write-Step 'Artefacts'
Get-ChildItem (Join-Path $root 'results') -Recurse -File | Select-Object -First 40 |
    ForEach-Object { Write-Host ("  " + $_.FullName.Substring($root.Length + 1)) }

if ($pipelineExit -ne 0) {
    Write-Host ''
    Write-Host 'Pipeline completed with failures. See VERIFY_ME.md and the messages above.' -ForegroundColor Yellow
    exit $pipelineExit
}

Write-Host ''
Write-Host 'Done. Read REPORT.md for the plain-language summary.' -ForegroundColor Green
