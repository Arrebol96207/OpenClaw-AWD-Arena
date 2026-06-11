<#
.SYNOPSIS
Runs the OpenClaw AWD Arena verification suite.

.DESCRIPTION
Checks patch formatting, default compose bind addresses, Python syntax, referee
container Python syntax, referee unit tests, target boundary tests, frontend
dependency audit, type checking, guarded production build, static smoke tests,
and optionally live smoke tests against running localhost services.

.PARAMETER SkipFrontend
Skip frontend audit, type check, build, and smoke tests.

.PARAMETER SkipReferee
Skip referee-engine container syntax check and unit tests.

.PARAMETER SkipTarget
Skip target CTF boundary tests.

.PARAMETER SkipSmoke
Skip Playwright smoke tests and run only the guarded frontend production build.

.PARAMETER SkipLiveSmoke
Run static smoke but skip live smoke against localhost:8000/8080.

.PARAMETER SkipAudit
Skip npm audit.

.PARAMETER Quick
Run only patch formatting, default compose bind address, and Python syntax checks.

.PARAMETER InstallFrontendDeps
Run `npm ci --no-audit --no-fund` in frontend before frontend checks.

.EXAMPLE
.\scripts\verify.ps1

.EXAMPLE
.\scripts\verify.ps1 -SkipLiveSmoke

.EXAMPLE
.\scripts\verify.ps1 -InstallFrontendDeps

.EXAMPLE
.\scripts\verify.ps1 -SkipReferee -SkipTarget -SkipFrontend

.EXAMPLE
.\scripts\verify.ps1 -Quick
#>

param(
  [switch]$Quick,
  [switch]$SkipFrontend,
  [switch]$SkipReferee,
  [switch]$SkipTarget,
  [switch]$SkipSmoke,
  [switch]$SkipLiveSmoke,
  [switch]$SkipAudit,
  [switch]$InstallFrontendDeps
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ($Quick) {
  $SkipFrontend = $true
  $SkipReferee = $true
  $SkipTarget = $true
}

function Invoke-Step {
  param(
    [string]$Name,
    [scriptblock]$Script
  )

  Write-Host ""
  Write-Host "==> $Name" -ForegroundColor Cyan
  & $Script
}

function Assert-DefaultComposeLocalhostBinds {
  $emptyEnvFile = [System.IO.FileInfo]::new([System.IO.Path]::GetTempFileName())
  $savedEnv = @{
    FRONTEND_BIND_HOST = $env:FRONTEND_BIND_HOST
    REFEREE_BIND_HOST = $env:REFEREE_BIND_HOST
    REFEREE_ALLOW_INSECURE_NO_AUTH = $env:REFEREE_ALLOW_INSECURE_NO_AUTH
    REFEREE_ALLOW_SHARED_NO_AUTH = $env:REFEREE_ALLOW_SHARED_NO_AUTH
    REFEREE_API_KEY = $env:REFEREE_API_KEY
  }

  try {
    Set-Content -LiteralPath $emptyEnvFile.FullName -Value "" -NoNewline
    foreach ($name in $savedEnv.Keys) {
      Remove-Item "Env:\$name" -ErrorAction SilentlyContinue
    }
    $composeConfig = docker compose --project-directory $repoRoot --env-file $emptyEnvFile.FullName config
    if ($LASTEXITCODE -ne 0) {
      throw "docker compose config failed while checking default localhost binds."
    }
  } finally {
    foreach ($name in $savedEnv.Keys) {
      if ($null -eq $savedEnv[$name]) {
        Remove-Item "Env:\$name" -ErrorAction SilentlyContinue
      } else {
        Set-Item "Env:\$name" $savedEnv[$name]
      }
    }
    Remove-Item -LiteralPath $emptyEnvFile.FullName -Force -ErrorAction SilentlyContinue
  }

  $configText = $composeConfig -join "`n"
  $requiredPatterns = @(
    'host_ip:\s+127\.0\.0\.1\s+target:\s+8000\s+published:\s+"8000"',
    'host_ip:\s+127\.0\.0\.1\s+target:\s+80\s+published:\s+"8080"'
  )

  foreach ($pattern in $requiredPatterns) {
    if ($configText -notmatch $pattern) {
      throw "Default docker compose ports must bind referee and frontend to 127.0.0.1 for dev no-auth safety."
    }
  }

  if ($configText -notmatch 'REFEREE_ALLOW_INSECURE_NO_AUTH:\s+"?1"?') {
    throw "Default docker compose config should keep local single-machine no-auth enabled for easy local play."
  }

  if ($configText -notmatch 'REFEREE_ALLOW_SHARED_NO_AUTH:\s+"?0"?') {
    throw "Default docker compose config must keep shared-network no-auth disabled."
  }
}

function Assert-DockerAvailable {
  try {
    docker version --format "{{.Server.Version}}" | Out-Null
  } catch {
    throw "Docker daemon is not reachable. Start Docker Desktop, then rerun this command. Use -Quick for checks that do not require Docker."
  }

  if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is not reachable. Start Docker Desktop, then rerun this command. Use -Quick for checks that do not require Docker."
  }
}

function Assert-LastCommandSucceeded {
  param([string]$Name)

  if ($LASTEXITCODE -ne 0) {
    throw "$Name failed with exit code $LASTEXITCODE."
  }
}

function Get-ProductionPythonFiles {
  param([switch]$ContainerPath)

  $refereeRoot = Join-Path $repoRoot "referee-engine"
  $excludedRoot = Join-Path $refereeRoot "tests"
  $files = Get-ChildItem -LiteralPath $refereeRoot -Recurse -File -Filter "*.py" |
    Where-Object {
      -not $_.FullName.StartsWith($excludedRoot, [StringComparison]::OrdinalIgnoreCase) -and
      -not $_.Name.StartsWith("test_", [StringComparison]::OrdinalIgnoreCase)
    }

  $files += Get-Item -LiteralPath (Join-Path $repoRoot "orchestrator/round_orchestrator.py")
  $files += Get-Item -LiteralPath (Join-Path $repoRoot "target-image/ctf/app.py")
  $files += Get-Item -LiteralPath (Join-Path $repoRoot "target-image/hardtest/awd_web_server.py")

  $files |
    Sort-Object FullName -Unique |
    ForEach-Object {
      $relativePath = Resolve-Path -Relative $_.FullName
      if ($ContainerPath) {
        $relativePath.Replace("\", "/").TrimStart("./")
      } else {
        $relativePath
      }
    }
}

function Assert-IgnoreFileContains {
  param(
    [string]$Path,
    [string[]]$Patterns
  )

  $content = Get-Content -LiteralPath $Path -ErrorAction Stop
  foreach ($pattern in $Patterns) {
    if ($content -notcontains $pattern) {
      throw "$Path must include '$pattern' to keep local artifacts out of source control and Docker build contexts."
    }
  }
}

function Assert-DockerfilesHaveIgnoreFiles {
  $dockerfiles = Get-ChildItem -LiteralPath $repoRoot -Recurse -Force -File |
    Where-Object {
      ($_.Name -eq "Dockerfile" -or $_.Name -like "Dockerfile.*") -and
      $_.FullName -notmatch "\\node_modules\\"
    }

  foreach ($dockerfile in $dockerfiles) {
    $ignorePath = Join-Path $dockerfile.DirectoryName ".dockerignore"
    if (-not (Test-Path -LiteralPath $ignorePath)) {
      $relativeDockerfile = Resolve-Path -Relative $dockerfile.FullName
      throw "$relativeDockerfile must have a sibling .dockerignore to keep local artifacts out of Docker build context."
    }
  }
}

function Assert-FrontendDevServerLocalByDefault {
  $viteConfigPath = Join-Path $repoRoot "frontend/vite.config.ts"
  $viteConfig = Get-Content -LiteralPath $viteConfigPath -Raw -ErrorAction Stop

  if ($viteConfig -match 'host:\s*true') {
    throw "frontend/vite.config.ts must not default the dev server to all interfaces while local no-auth is enabled."
  }

  if ($viteConfig -notmatch "process\.env\.VITE_DEV_HOST\s*\|\|\s*'127\.0\.0\.1'") {
    throw "frontend/vite.config.ts should default Vite dev server host to 127.0.0.1 and require VITE_DEV_HOST for shared access."
  }
}

function Assert-NoStrayTopLevelEmptyFiles {
  $scanRoots = @(
    $repoRoot,
    (Join-Path $repoRoot "frontend")
  )
  $allowedNames = @(
    ".gitkeep"
  )

  $strayFiles = @()
  foreach ($root in $scanRoots) {
    $strayFiles += Get-ChildItem -LiteralPath $root -Force -File |
      Where-Object {
        $_.Length -eq 0 -and
        $allowedNames -notcontains $_.Name
      } |
      ForEach-Object { Resolve-Path -Relative $_.FullName }
  }

  if ($strayFiles.Count -gt 0) {
    throw "Unexpected empty top-level file(s), likely from failed shell redirection or local tooling: $($strayFiles -join ', ')"
  }
}

function Assert-PathsNotTracked {
  param([string[]]$Paths)

  $tracked = @(git ls-files -- @Paths)
  Assert-LastCommandSucceeded "Check tracked local artifact paths"
  if ($tracked.Count -gt 0) {
    throw "Local artifact path(s) must not be tracked by git: $($tracked -join ', ')"
  }
}

function Assert-IgnoredFilesNotTracked {
  $ignoredTrackedFiles = @()
  $trackedFiles = @(git ls-files)
  Assert-LastCommandSucceeded "List tracked files"

  foreach ($trackedFile in $trackedFiles) {
    git check-ignore -q --no-index -- $trackedFile
    if ($LASTEXITCODE -eq 0) {
      $ignoredTrackedFiles += $trackedFile
    } elseif ($LASTEXITCODE -gt 1) {
      throw "git check-ignore failed for tracked file '$trackedFile' with exit code $LASTEXITCODE."
    }
  }

  if ($ignoredTrackedFiles.Count -gt 0) {
    throw "Tracked file(s) match .gitignore rules and should be removed from the index or unignored explicitly: $($ignoredTrackedFiles -join ', ')"
  }
}

Push-Location $repoRoot
try {
  Invoke-Step "Patch format check" {
    git -c core.safecrlf=false diff --check
    Assert-LastCommandSucceeded "Patch format check"
  }

  Invoke-Step "Default compose localhost bind check" {
    Assert-DefaultComposeLocalhostBinds
    Assert-FrontendDevServerLocalByDefault
  }

  Invoke-Step "Local artifact ignore check" {
    Assert-NoStrayTopLevelEmptyFiles
    Assert-DockerfilesHaveIgnoreFiles
    Assert-IgnoreFileContains `
      -Path (Join-Path $repoRoot ".gitignore") `
      -Patterns @(".env", ".env.*", "!.env.example", "__pycache__/", ".pytest_cache/", ".pytest-tmp*/", "node_modules/", "dist/", "test-results/", "playwright-report/", "referee-engine/openclaw.db", "output/")
    Assert-IgnoreFileContains `
      -Path (Join-Path $repoRoot "frontend/.dockerignore") `
      -Patterns @("node_modules/", "dist/", ".pytest-tmp*/", "test-results/", "playwright-report/", ".env")
    Assert-IgnoreFileContains `
      -Path (Join-Path $repoRoot "referee-engine/.dockerignore") `
      -Patterns @("__pycache__/", ".pytest_cache/", ".pytest-tmp*/", "tests/", "test_*.py", "coverage/", "output/", ".env")
    Assert-IgnoreFileContains `
      -Path (Join-Path $repoRoot "agent-image/.dockerignore") `
      -Patterns @(".env", "__pycache__/", ".pytest_cache/", ".pytest-tmp*/", "node_modules/", "dist/", "coverage/")
    Assert-IgnoreFileContains `
      -Path (Join-Path $repoRoot "target-image/ctf/.dockerignore") `
      -Patterns @(".env", "__pycache__/", ".pytest_cache/", ".pytest-tmp*/", "tests/", "coverage/")
    Assert-IgnoreFileContains `
      -Path (Join-Path $repoRoot "target-image/.dockerignore") `
      -Patterns @(".env", "__pycache__/", "**/__pycache__/", ".pytest_cache/", "**/.pytest_cache/", ".pytest-tmp*/", "**/.pytest-tmp*/", "tests/", "**/tests/", "coverage/")
    Assert-IgnoreFileContains `
      -Path (Join-Path $repoRoot "target-image/hardtest/.dockerignore") `
      -Patterns @(".env", "__pycache__/", ".pytest_cache/", ".pytest-tmp*/", "tests/", "coverage/")
    Assert-IgnoreFileContains `
      -Path (Join-Path $repoRoot "referee-engine/runtime/hermes/.dockerignore") `
      -Patterns @(".env", "__pycache__/", ".pytest_cache/", ".pytest-tmp*/", "tests/", "coverage/")
    Assert-PathsNotTracked -Paths @(
      ".env",
      "referee-engine/openclaw.db",
      "referee-engine/templates.json",
      "frontend/dist",
      "frontend/test-results",
      "playwright-report",
      "output"
    )
    Assert-IgnoredFilesNotTracked
  }

  Invoke-Step "Python syntax check" {
    $pythonFiles = @(Get-ProductionPythonFiles)
    python -m py_compile @pythonFiles
    Assert-LastCommandSucceeded "Python syntax check"
  }

  if (-not $SkipReferee) {
    Invoke-Step "Docker availability check" {
      Assert-DockerAvailable
    }

    Invoke-Step "Referee container Python syntax check" {
      $pythonFiles = @(Get-ProductionPythonFiles -ContainerPath)
      docker compose `
        --project-directory $repoRoot `
        run --rm --no-deps `
        -v "${repoRoot}:/workspace" `
        -w /workspace `
        referee-engine `
        python -m py_compile @pythonFiles
      Assert-LastCommandSucceeded "Referee container Python syntax check"
    }

    Invoke-Step "Referee unit tests" {
      & (Join-Path $PSScriptRoot "test-referee.ps1")
      Assert-LastCommandSucceeded "Referee unit tests"
    }
  }

  if (-not $SkipTarget) {
    if ($SkipReferee) {
      Invoke-Step "Docker availability check" {
        Assert-DockerAvailable
      }
    }

    Invoke-Step "Target CTF boundary tests" {
      try {
        docker compose `
          --project-directory $repoRoot `
          run --rm --no-deps `
          -v "${repoRoot}:/workspace" `
          -w /workspace/target-image/ctf `
          -e "PYTHONPATH=/workspace:/workspace/referee-engine" `
          referee-engine `
          python -m pytest tests/test_stage1_boundaries.py -q --basetemp=.pytest-tmp-target
        Assert-LastCommandSucceeded "Target CTF boundary tests"
      } finally {
        Remove-Item -LiteralPath (Join-Path $repoRoot "target-image/ctf/.pytest-tmp-target") -Recurse -Force -ErrorAction SilentlyContinue
      }
    }
  }

  if (-not $SkipFrontend) {
    Push-Location (Join-Path $repoRoot "frontend")
    try {
      if ($InstallFrontendDeps) {
        Invoke-Step "Frontend dependency install" {
          cmd /c npm ci --no-audit --no-fund
          Assert-LastCommandSucceeded "Frontend dependency install"
        }
      } elseif (-not (Test-Path (Join-Path (Get-Location) "node_modules"))) {
        throw "frontend/node_modules is missing. Run npm ci in frontend, or rerun .\scripts\verify.ps1 -InstallFrontendDeps."
      }

      if (-not $SkipAudit) {
        Invoke-Step "Frontend dependency audit" {
          cmd /c npm audit --package-lock-only --audit-level=moderate
          Assert-LastCommandSucceeded "Frontend dependency audit"
        }
      }

      Invoke-Step "Frontend type check" {
        cmd /c npx tsc --noEmit
        Assert-LastCommandSucceeded "Frontend type check"
      }

      if (-not $SkipSmoke) {
        Invoke-Step "Frontend static smoke tests" {
          cmd /c npm run test:smoke:static
          Assert-LastCommandSucceeded "Frontend static smoke tests"
        }
      } else {
        Invoke-Step "Frontend production build" {
          cmd /c npm run build:guarded
          Assert-LastCommandSucceeded "Frontend production build"
        }
      }

      if ((-not $SkipSmoke) -and (-not $SkipLiveSmoke)) {
        Invoke-Step "Frontend live smoke tests" {
          cmd /c npm run test:smoke:live
          Assert-LastCommandSucceeded "Frontend live smoke tests"
        }
      }
    } finally {
      Pop-Location
    }
  }

  Write-Host ""
  Write-Host "All selected verification steps passed." -ForegroundColor Green
} finally {
  Pop-Location
}
