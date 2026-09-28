<#
.SYNOPSIS
  Installs LexiVox on this Windows machine, choosing the speech engines that
  match its hardware.

.DESCRIPTION
  - Always:        faster-whisper (fast int8 CPU engine; works on any PC)
  - Intel CPU/GPU: + OpenVINO (Intel Arc / Iris Xe GPU, and a fast CPU path)
  - NVIDIA GPU:    + CUDA libraries for faster-whisper (~1.3 GB)
  - AMD GPU:       CPU engines only for now (a Vulkan engine is on the roadmap)
  CUDA packages are never installed on a machine without an NVIDIA GPU.

  Needs: uv, ffmpeg on PATH. Optional: Ollama (for AI polish).
  Models are downloaded from inside the app, or copied from another PC (see README).

.PARAMETER Engines
  Override detection, e.g. -Engines cpu,intel   (choices: cpu, intel, nvidia)

.PARAMETER NoShortcut
  Don't create the Start menu shortcut.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#>
param(
    [ValidateSet("cpu", "intel", "nvidia")]
    [string[]]$Engines,
    [switch]$NoShortcut,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

# ---- prerequisites -----------------------------------------------------------------
$missing = @()
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { $missing += "uv      ->  winget install astral-sh.uv" }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { $missing += "ffmpeg  ->  winget install Gyan.FFmpeg" }
if ($missing) {
    Say "Please install these first, then open a new terminal and run this script again:" Yellow
    $missing | ForEach-Object { Say "  $_" Yellow }
    exit 1
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Say "Note: Ollama isn't installed. Everything works without it except AI polish." DarkYellow
    Say "      To add it later: winget install Ollama.Ollama, then: ollama pull qwen2.5:7b" DarkYellow
}

# ---- hardware detection ------------------------------------------------------------
$cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name.Trim()
$gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
$npu = @(Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "AI Boost|Neural Processing|\bNPU\b" } | ForEach-Object { $_.Name })

$hasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue) -or ($gpus -match "NVIDIA")
$hasIntelGpu = [bool]($gpus -match "Intel.*(Arc|Iris|UHD|Graphics)")
$hasAmdGpu = [bool]($gpus -match "AMD|Radeon")
$isIntelCpu = $cpu -match "Intel"

Say "CPU:  $cpu"
$gpus | ForEach-Object { Say "GPU:  $_" }
$npu | ForEach-Object { Say "NPU:  $_" }

$reasons = @()
if (-not $Engines) {
    $Engines = @("cpu")
    $reasons += "cpu     : faster-whisper, works on every PC"
    if ($hasIntelGpu -or $isIntelCpu) {
        $Engines += "intel"
        $reasons += "intel   : OpenVINO for the Intel " + $(if ($hasIntelGpu) { "GPU" } else { "CPU" })
    }
    if ($hasNvidia) {
        $Engines += "nvidia"
        $reasons += "nvidia  : CUDA libraries for the NVIDIA GPU (~1.3 GB)"
    }
    if ($hasAmdGpu -and -not $hasNvidia) {
        $reasons += "(AMD GPU found: using CPU engines; GPU support for AMD is planned)"
    }
} else {
    $reasons += "chosen with -Engines"
}
Say ""
Say "Installing engines: $($Engines -join ', ')" Cyan
$reasons | ForEach-Object { Say "  $_" }

$uvArgs = @("sync", "--no-dev") + ($Engines | ForEach-Object { "--extra"; $_ })
Say ""
Say "uv $($uvArgs -join ' ')" DarkGray
if ($DryRun) { exit 0 }
& uv @uvArgs
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

if (-not $NoShortcut) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "make-shortcut.ps1")
}

Say ""
Say "Done. Start 'LexiVox' from the Start menu (or run scripts\run.ps1)." Green
Say "First time: open Settings > Speech recognition and download a model," Green
Say "or copy a models folder from another PC into %LOCALAPPDATA%\LexiVox\models and click Rescan." Green
