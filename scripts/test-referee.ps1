<#
.SYNOPSIS
Runs referee-engine pytest inside the Docker Compose referee image.

.DESCRIPTION
Mounts the working tree into the referee-engine container so local tests are available
even though production images do not package tests. The script defaults to
`tests/unit -q`, forwards remaining arguments to pytest, and removes one-off
referee test containers when the run finishes. It checks Docker daemon availability
up front and preserves the original pytest failure if cleanup also fails.

.PARAMETER CleanupOneOff
Only remove leftover one-off referee-engine test containers for this Compose project.

.PARAMETER PytestArgs
Arguments forwarded to `python -m pytest`. In PowerShell, use `--%` after the script
name for complex pytest selectors such as `file.py::test_name` or `-k "expr"`.

.EXAMPLE
.\scripts\test-referee.ps1

.EXAMPLE
.\scripts\test-referee.ps1 --% tests/unit/test_submission_flow.py::test_success_submission_updates_score_from_persisted_submissions_not_runtime_buffer -q

.EXAMPLE
.\scripts\test-referee.ps1 -CleanupOneOff
#>

param(
  [switch]$CleanupOneOff,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$projectName = "openclaw-awd-arena"

function Assert-LastCommandSucceeded {
  param([string]$Name)

  if ($LASTEXITCODE -ne 0) {
    throw "$Name failed with exit code $LASTEXITCODE."
  }
}

function Assert-DockerAvailable {
  try {
    docker version --format "{{.Server.Version}}" | Out-Null
  } catch {
    throw "Docker daemon is not reachable. Start Docker Desktop, then rerun this command."
  }

  if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is not reachable. Start Docker Desktop, then rerun this command."
  }
}

function Remove-OneOffRefereeContainers {
  $containerIds = @(
    docker ps -a `
      --filter "label=com.docker.compose.project=$projectName" `
      --filter "label=com.docker.compose.service=referee-engine" `
      --filter "label=com.docker.compose.oneoff=True" `
      --format "{{.ID}}"
  )
  Assert-LastCommandSucceeded "List one-off referee containers"

  if ($containerIds.Count -eq 0) {
    Write-Host "No one-off referee test containers to clean."
    return
  }

  Write-Host "Removing $($containerIds.Count) one-off referee test container(s)..."
  docker rm -f $containerIds | Out-Null
  Assert-LastCommandSucceeded "Remove one-off referee containers"
}

function Test-HasPytestBaseTemp {
  param([string[]]$Args)

  foreach ($arg in $Args) {
    if ($arg -eq "--basetemp" -or $arg.StartsWith("--basetemp=")) {
      return $true
    }
  }
  return $false
}

Assert-DockerAvailable

if ($CleanupOneOff) {
  Remove-OneOffRefereeContainers
  return
}

if (-not $PytestArgs -or $PytestArgs.Count -eq 0) {
  $PytestArgs = @("tests/unit", "-q")
}

if (-not (Test-HasPytestBaseTemp -Args $PytestArgs)) {
  $PytestArgs += "--basetemp=.pytest-tmp-referee"
}

$pytestBaseTempPath = Join-Path $repoRoot "referee-engine/.pytest-tmp-referee"
$pytestError = $null
$cleanupError = $null

try {
  docker compose `
    --project-directory $repoRoot `
    run --rm --no-deps `
    -v "${repoRoot}:/workspace" `
    -w /workspace/referee-engine `
    -e "PYTHONPATH=/workspace:/workspace/referee-engine" `
    referee-engine `
    python -m pytest @PytestArgs
  Assert-LastCommandSucceeded "Referee pytest"
} catch {
  $pytestError = $_
} finally {
  try {
    Remove-OneOffRefereeContainers
  } catch {
    $cleanupError = $_
    Write-Warning "Cleanup of one-off referee test containers failed: $($_.Exception.Message)"
  }
  Remove-Item -LiteralPath $pytestBaseTempPath -Recurse -Force -ErrorAction SilentlyContinue
}

if ($pytestError) {
  throw $pytestError
}

if ($cleanupError) {
  throw $cleanupError
}
