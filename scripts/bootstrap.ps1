#Requires -Version 5.1
<#
  Plutus - one-command setup and launch for Windows.

  Written for a machine with nothing on it. Every prerequisite is checked,
  installed if missing, and skipped if already present, so running this twice
  is safe and the second run is fast.

    .\scripts\bootstrap.ps1             install what's missing, then start
    .\scripts\bootstrap.ps1 -NoStart    install only
    .\scripts\bootstrap.ps1 -Stop       stop everything this started
#>
[CmdletBinding()]
param(
  [switch]$NoStart,
  [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$BackendPort  = 8001
$FrontendPort = 5173
$RunDir = Join-Path $Root '.run'
if (-not (Test-Path $RunDir)) { New-Item -ItemType Directory -Path $RunDir | Out-Null }

# --- output ---------------------------------------------------------------

function Write-Step { param($m) Write-Host "`n==> " -ForegroundColor Blue -NoNewline; Write-Host $m -ForegroundColor White }
function Write-Ok   { param($m) Write-Host "  " -NoNewline; Write-Host "OK " -ForegroundColor Green -NoNewline; Write-Host $m }
function Write-Info { param($m) Write-Host "   $m" -ForegroundColor DarkGray }
function Write-Warn { param($m) Write-Host "  ! " -ForegroundColor Yellow -NoNewline; Write-Host $m }
function Die        { param($m) Write-Host "`n  x $m`n" -ForegroundColor Red; exit 1 }

function Test-Command { param($n) return [bool](Get-Command $n -ErrorAction SilentlyContinue) }

# Native tools write progress to stderr as a matter of course - docker
# compose reports "Container ... Running" that way. With
# $ErrorActionPreference = 'Stop', PowerShell promotes any native stderr line
# to a terminating error, so a perfectly successful `docker compose up`
# aborts the script. Exit code is the only honest signal here.
function Invoke-Native {
    param([scriptblock]$Command, [string]$What = 'command')
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Die "$What failed (exit $LASTEXITCODE)." }
    } finally {
        $ErrorActionPreference = $previous
    }
}

# `winget install` reports success before the new command is on this
# process's PATH, so refresh it from the registry rather than telling the
# user to open a new terminal.
function Update-PathFromRegistry {
  $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
  $env:Path = "$machine;$user"
}

# --- stop -----------------------------------------------------------------

function Stop-All {
  Write-Step 'Stopping Plutus'
  foreach ($name in 'backend', 'frontend') {
    $pidFile = Join-Path $RunDir "$name.pid"
    if (Test-Path $pidFile) {
      $procId = (Get-Content $pidFile).Trim()
      if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Ok "stopped $name (pid $procId)"
      }
      Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
  }
  # Anything still holding the ports, e.g. a server started by hand.
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in $BackendPort, $FrontendPort } |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

  if (Test-Command docker) {
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
      # `stop`, not `down`: it clears the restart policy without deleting the
      # volumes your filings and vectors live in.
      Invoke-Native { docker compose stop } 'Stopping data services'
      Write-Ok 'stopped database and vector store'
    }
  }
  Write-Host "`n  Plutus stopped. Your data is kept.`n"
  exit 0
}

if ($Stop) { Stop-All }

# --- prerequisites --------------------------------------------------------

function Install-WithWinget {
  param($Id, $Label)
  if (-not (Test-Command winget)) {
    Die "winget is not available. Install '$Label' manually, or update App Installer from the Microsoft Store, then re-run."
  }
  Write-Info "installing $Label (winget)..."
  Invoke-Native { winget install --id $Id --exact --silent --accept-source-agreements --accept-package-agreements } "Installing $Label"
  Update-PathFromRegistry
}

$script:Python = $null

function Initialize-Python {
  # 3.11+ is required: the code uses `X | Y` unions and `match` freely.
  foreach ($candidate in 'python', 'python3', 'py') {
    if (Test-Command $candidate) {
      $v = & $candidate -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
      if ($LASTEXITCODE -eq 0 -and $v) {
        $parts = $v.Split('.')
        if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 11) {
          $script:Python = $candidate
          Write-Ok "Python $v"
          return
        }
      }
    }
  }
  Write-Step 'Installing Python 3.12'
  Install-WithWinget 'Python.Python.3.12' 'Python 3.12'
  foreach ($candidate in 'python', 'python3') {
    if (Test-Command $candidate) { $script:Python = $candidate; break }
  }
  if (-not $script:Python) { Die 'Python installed but not on PATH. Open a new terminal and re-run.' }
  Write-Ok 'Python installed'
}

function Initialize-Node {
  if (Test-Command node) {
    $major = ((node -v) -replace '^v', '').Split('.')[0]
    if ([int]$major -ge 18) { Write-Ok "Node $(node -v)"; return }
  }
  Write-Step 'Installing Node.js'
  Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js LTS'
  if (-not (Test-Command node)) { Die 'Node installed but not on PATH. Open a new terminal and re-run.' }
  Write-Ok "Node $(node -v)"
}

function Initialize-Docker {
  if (-not (Test-Command docker)) {
    Write-Step 'Installing Docker Desktop'
    Install-WithWinget 'Docker.DockerDesktop' 'Docker Desktop'
    Write-Warn 'Docker Desktop usually needs one reboot before it will run.'
    Write-Warn 'If the wait below times out, restart Windows and re-run this script.'
  }

  docker info 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { Write-Ok 'Docker is running'; return }

  Write-Step 'Starting Docker Desktop'
  $exe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
  # Start-Process only. Docker Desktop's -Shutdown flag *launches* the app,
  # which is a confusing way to discover that it is already running.
  if (Test-Path $exe) { Start-Process $exe | Out-Null }
  Write-Info 'waiting for the Docker engine (this can take a few minutes on first run)...'
  foreach ($_ in 1..150) {
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok 'Docker is running'; return }
    Start-Sleep -Seconds 2
  }
  Die 'Docker did not start. Open Docker Desktop manually, wait for it to say "Engine running", then re-run.'
}

# --- configuration --------------------------------------------------------

function Initialize-Env {
  if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Ok 'created .env from .env.example'
  }

  $content = Get-Content .env -Raw
  if ($content -match '(?m)^LLM_API_KEY=.+') { Write-Ok 'LLM API key configured'; return }

  Write-Warn 'No LLM API key set yet.'
  Write-Host @'

  Plutus answers questions with a hosted model. Get a free key from either:

    Google AI Studio   https://aistudio.google.com/apikey     (generous free tier)
    Groq               https://console.groq.com               (fast, smaller free tier)

'@
  $key = Read-Host '  Paste your API key (or press Enter to skip and add it later)'
  if ([string]::IsNullOrWhiteSpace($key)) {
    Write-Warn 'Skipped. Add LLM_API_KEY to .env before asking questions.'
    return
  }

  if ($key.StartsWith('gsk_')) {
    $base = 'https://api.groq.com/openai/v1'; $model = 'openai/gpt-oss-120b'
  } else {
    $base = 'https://generativelanguage.googleapis.com/v1beta/openai/'; $model = 'gemini-3.1-flash-lite'
  }
  foreach ($pair in @(@('LLM_API_KEY', $key), @('LLM_BASE_URL', $base), @('LLM_MODEL', $model))) {
    $name, $value = $pair
    if ($content -match "(?m)^$name=") { $content = $content -replace "(?m)^$name=.*$", "$name=$value" }
    else { $content += "`n$name=$value`n" }
  }
  # UTF8Encoding($false): Set-Content -Encoding utf8 writes a BOM on Windows
  # PowerShell 5.1, and a BOM in a dotenv file is read as part of the first
  # key name.
  [System.IO.File]::WriteAllText((Join-Path $Root '.env'), $content, (New-Object System.Text.UTF8Encoding($false)))
  Write-Ok 'API key saved to .env'
}

# --- install --------------------------------------------------------------

function Initialize-Backend {
  Write-Step 'Backend'
  if (-not (Test-Path backend\.venv)) {
    & $script:Python -m venv backend\.venv
    Write-Ok 'created virtual environment'
  } else {
    Write-Info 'virtual environment already present'
  }
  $venvPy = Join-Path $Root 'backend\.venv\Scripts\python.exe'
  & $venvPy -m pip install --quiet --upgrade pip
  Write-Info 'installing Python packages (a few minutes on first run)...'
  & $venvPy -m pip install --quiet -r backend\requirements.txt
  if ($LASTEXITCODE -ne 0) { Die 'Python package install failed.' }
  Write-Ok 'Python packages installed'
}

# Acceleration is offered, never assumed: the runtimes are ~1GB downloads and
# the right one depends on the vendor. On Windows an AMD or Intel card needs
# DirectML - CUDA will not drive it.
function Suggest-Acceleration {
  $venvPy = Join-Path $Root 'backend\.venv\Scripts\python.exe'
  $active = & $venvPy -c "
import onnxruntime as ort
accel=[p for p in ort.get_available_providers() if p not in ('CPUExecutionProvider','AzureExecutionProvider')]
print(accel[0] if accel else '')" 2>$null
  if ($active) { Write-Ok "hardware acceleration available: $active"; return }

  # Ask Windows what the display adapters actually are, rather than guessing
  # from whichever vendor tool happens to be on PATH.
  $gpus = (Get-CimInstance Win32_VideoController -EA SilentlyContinue).Name -join ' '
  if ($gpus -match 'NVIDIA') {
    Write-Warn 'An NVIDIA GPU is present but the CPU runtime is installed.'
    Write-Info 'Much faster indexing:  pip install onnxruntime-gpu'
  } elseif ($gpus -match 'AMD|Radeon|Intel Arc') {
    Write-Warn 'An AMD/Intel GPU is present but the CPU runtime is installed.'
    Write-Info 'On Windows these need DirectML:  pip install onnxruntime-directml'
    Write-Info 'See backendequirements-accelerate.txt'
  }
}

function Initialize-Frontend {
  Write-Step 'Frontend'
  if (Test-Path frontend\node_modules) {
    Write-Info 'npm packages already present'
  } else {
    Write-Info 'installing npm packages...'
    Push-Location frontend
    Invoke-Native { npm install --silent } 'npm install'
    Pop-Location
  }
  Write-Ok 'npm packages installed'
}

# --- run ------------------------------------------------------------------

function Wait-ForUrl {
  param($Url, $Tries = 120)
  foreach ($_ in 1..$Tries) {
    try { Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true } catch { }
    Start-Sleep -Seconds 2
  }
  return $false
}

function Start-Services {
  Write-Step 'Data services'
  Invoke-Native { docker compose up -d } 'Starting Postgres and Qdrant'
  Write-Info 'waiting for Postgres...'
  foreach ($_ in 1..60) {
    $status = docker compose ps --format '{{.Service}} {{.Status}}' 2>$null
    if ($status -match 'postgres.*healthy') { break }
    Start-Sleep -Seconds 2
  }
  Write-Ok 'Postgres and Qdrant are up'

  Write-Step 'Starting Plutus'
  $venvPy = Join-Path $Root 'backend\.venv\Scripts\python.exe'
  $backend = Start-Process -FilePath $venvPy `
    -ArgumentList '-u', '-m', 'uvicorn', 'app.main:app', '--port', $BackendPort, '--host', '127.0.0.1' `
    -WorkingDirectory (Join-Path $Root 'backend') -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $RunDir 'backend.log') `
    -RedirectStandardError  (Join-Path $RunDir 'backend.err.log')
  $backend.Id | Out-File (Join-Path $RunDir 'backend.pid') -Encoding ascii

  Write-Info 'backend starting (it downloads ~150MB of models on the first run)...'
  if (-not (Wait-ForUrl "http://127.0.0.1:$BackendPort/health" 180)) {
    Die "Backend did not come up. See $RunDir\backend.err.log"
  }
  Write-Ok "backend ready on http://localhost:$BackendPort"

  $frontend = Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/c', "npm run dev -- --port $FrontendPort --strictPort" `
    -WorkingDirectory (Join-Path $Root 'frontend') -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $RunDir 'frontend.log') `
    -RedirectStandardError  (Join-Path $RunDir 'frontend.err.log')
  $frontend.Id | Out-File (Join-Path $RunDir 'frontend.pid') -Encoding ascii

  # Vite binds IPv6 loopback, so 127.0.0.1 can refuse while localhost works.
  if (-not (Wait-ForUrl "http://localhost:$FrontendPort" 60)) {
    Die "Frontend did not come up. See $RunDir\frontend.err.log"
  }
  Write-Ok 'frontend ready'
}

function Show-Ready {
  Write-Host ''
  Write-Host '  ------------------------------------------------' -ForegroundColor Green
  Write-Host '    Plutus is up and ready to use'                   -ForegroundColor Green
  Write-Host '  ------------------------------------------------' -ForegroundColor Green
  Write-Host ''
  Write-Host "     Open   http://localhost:$FrontendPort" -ForegroundColor White
  Write-Host ''
  Write-Host '     Add a filing (PDF, HTM or HTML), then ask about it.'
  Write-Host '     Every answer cites the page it came from - or says it'
  Write-Host '     could not find one.'
  Write-Host ''
  Write-Host "     Logs    $RunDir\"
  Write-Host '     Stop    .\scripts\bootstrap.ps1 -Stop'
  Write-Host ''
  Start-Process "http://localhost:$FrontendPort" | Out-Null
}

# --- main -----------------------------------------------------------------

Write-Host "`n  Plutus - setup for Windows" -ForegroundColor White

Write-Step 'Checking prerequisites'
Initialize-Python
Initialize-Node
Initialize-Docker

Initialize-Env
Initialize-Backend
Suggest-Acceleration
Initialize-Frontend

if ($NoStart) {
  Write-Host "`n  Setup complete. Start it with .\scripts\bootstrap.ps1`n"
  exit 0
}

Start-Services
Show-Ready
