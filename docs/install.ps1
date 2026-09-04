# selects one-line installer (Windows, PowerShell).
#
#   irm https://bihanikeshav.github.io/selects/install.ps1 | iex
#
# Installs through uv, so there is no downloaded .exe for SmartScreen to flag
# and nothing to code-sign. uv brings its own managed Python, so you do not
# need Python installed first. Re-running upgrades selects in place.
$ErrorActionPreference = 'Stop'

function Say($m) { Write-Host "==> $m" -ForegroundColor Blue }

# 1. Ensure uv. The official installer via irm | iex is not SmartScreen-flagged
#    the way a downloaded installer is.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Say 'Installing uv (Python toolchain)...'
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}

# uv and the tools it installs land in %USERPROFILE%\.local\bin. Put that on
# PATH for the launch below even if this session predates uv.
$binDirs = @("$env:USERPROFILE\.local\bin", "$env:USERPROFILE\.cargo\bin")
foreach ($d in $binDirs) { if (Test-Path $d) { $env:PATH = "$d;$env:PATH" } }

# 2. Install selects with the on-device AI extra (onnxruntime,
#    insightface, sklearn, …). --force makes a re-run upgrade an existing install.
Say 'Installing selects (pulls the AI stack; first launch also downloads models)...'
uv tool install --python 3.11 --force "selects[ml]"

# 3. Launch. The AI model weights (~3.5 GB) download from inside the app's
#    first-run setup screen, with a progress bar - not here.
Say 'Starting selects...'
selects serve
