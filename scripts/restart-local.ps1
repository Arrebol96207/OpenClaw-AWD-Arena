<#
.SYNOPSIS
Restarts the local OpenClaw AWD Arena stack and verifies basic health.

.DESCRIPTION
Builds and starts the Docker Compose services for local single-machine play,
waits for the referee health endpoint, prints auth/deployment mode, and can
optionally build the local Agent image and run frontend live smoke tests.

.PARAMETER BuildAgentImage
Build openclaw/local-agent:ssh before restarting the stack.

.PARAMETER SkipComposeBuild
Run docker compose up without --build.

.PARAMETER RunLiveSmoke
Run frontend npm live smoke tests after the stack is healthy.

.PARAMETER SkipHealthWait
Do not wait for http://localhost:8000/health.

.PARAMETER HealthTimeoutSeconds
Maximum seconds to wait for the referee health endpoint. Defaults to 60.

.EXAMPLE
.\scripts\restart-local.ps1

.EXAMPLE
.\scripts\restart-local.ps1 -BuildAgentImage -RunLiveSmoke

.EXAMPLE
.\scripts\restart-local.ps1 -SkipComposeBuild
#>

param(
  [switch]$BuildAgentImage,
  [switch]$SkipComposeBuild,
  [switch]$RunLiveSmoke,
  [switch]$SkipHealthWait,
  [int]$HealthTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontendUrl = "http://localhost:8080"
$refereeUrl = "http://localhost:8000"

function Invoke-Step {
  param(
    [string]$Name,
    [scriptblock]$Script
  )

  Write-Host ""
  Write-Host "==> $Name" -ForegroundColor Cyan
  & $Script
}

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
    throw "Docker daemon is not reachable. Start Docker Desktop, then rerun this script."
  }

  if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is not reachable. Start Docker Desktop, then rerun this script."
  }
}

function Invoke-CurlJson {
  param([string]$Url)

  $text = curl.exe -fsS $Url
  Assert-LastCommandSucceeded "GET $Url"
  try {
    return $text | ConvertFrom-Json
  } catch {
    return $text
  }
}

function Wait-RefereeHealth {
  $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
  $lastError = $null

  while ((Get-Date) -lt $deadline) {
    try {
      $health = Invoke-CurlJson "$refereeUrl/health"
      if ($health.status -eq "healthy") {
        return $health
      }
      $lastError = "status=$($health.status)"
    } catch {
      $lastError = $_.Exception.Message
    }

    Start-Sleep -Seconds 2
  }

  throw "Referee health did not become healthy within $HealthTimeoutSeconds seconds. Last error: $lastError"
}

function Write-EndpointSummary {
  param($Health)

  Write-Host "Referee: $refereeUrl"
  Write-Host "Frontend: $frontendUrl"
  if ($Health) {
    Write-Host "Health: status=$($Health.status), auth_mode=$($Health.auth_mode), exposure=$($Health.deployment_exposure), active_matches=$($Health.active_matches)"
  }

  try {
    $auth = Invoke-CurlJson "$refereeUrl/api/auth/status"
    Write-Host "Auth: authenticated=$($auth.authenticated), insecure_dev_auth=$($auth.insecure_dev_auth), no_auth_local_only=$($auth.no_auth_local_only)"
  } catch {
    Write-Warning "Could not read auth status: $($_.Exception.Message)"
  }
}

Push-Location $repoRoot
try {
  Invoke-Step "Docker availability check" {
    Assert-DockerAvailable
  }

  if ($BuildAgentImage) {
    Invoke-Step "Build local Agent image" {
      docker build -t openclaw/local-agent:ssh -f agent-image/Dockerfile.local agent-image
      Assert-LastCommandSucceeded "Build local Agent image"
    }
  }

  Invoke-Step "Start local stack" {
    if ($SkipComposeBuild) {
      docker compose up -d
    } else {
      docker compose up -d --build
    }
    Assert-LastCommandSucceeded "Start local stack"
  }

  $health = $null
  if (-not $SkipHealthWait) {
    Invoke-Step "Wait for referee health" {
      $script:health = Wait-RefereeHealth
    }
  }

  Invoke-Step "Service status" {
    docker compose ps
    Assert-LastCommandSucceeded "docker compose ps"
    Write-EndpointSummary $script:health
  }

  if ($RunLiveSmoke) {
    Invoke-Step "Frontend live smoke tests" {
      $frontendDir = Join-Path $repoRoot "frontend"
      if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
        throw "frontend/node_modules is missing. Run 'cd frontend; npm ci' first, or rerun without -RunLiveSmoke."
      }

      Push-Location $frontendDir
      try {
        cmd /c npm run test:smoke:live
        Assert-LastCommandSucceeded "Frontend live smoke tests"
      } finally {
        Pop-Location
      }
    }
  }

  Write-Host ""
  Write-Host "Local stack is ready." -ForegroundColor Green
} finally {
  Pop-Location
}
