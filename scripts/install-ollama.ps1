<#
.SYNOPSIS
  Installs Ollama and a chat model so LexiVox's "AI polish" works on this PC.

.DESCRIPTION
  LexiVox itself doesn't need Ollama: dictation and file transcription work without it.
  Only the AI polish shortcut does. This script:
    1. installs Ollama if it's missing (winget, or the official signed installer)
    2. makes sure the Ollama server is running on this PC only (127.0.0.1)
    3. adds a chat model, picked by memory unless you choose one:
         16 GB RAM or more -> qwen2.5:7b  (4.7 GB, best results)
         less              -> llama3.2:3b (2.0 GB, about twice as fast)
       either downloaded from Ollama, or copied from another PC with -ModelsFrom
    4. runs a short test request

  No admin rights are needed: Ollama installs per user.

.PARAMETER Model
  Chat model to add, e.g. qwen2.5:7b or llama3.2:3b. Default: chosen by RAM.

.PARAMETER ModelsFrom
  Copy models instead of downloading them: a copy of another PC's
  %USERPROFILE%\.ollama\models folder (USB drive, network share...).

.PARAMETER SkipModel
  Install Ollama only.

.PARAMETER AcceptAgreements
  Pass winget's agreement flags so the install doesn't stop to ask.

.PARAMETER DryRun
  Show what would happen without installing or downloading anything.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-ollama.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-ollama.ps1 -ModelsFrom E:\ollama-models
#>
param(
    [string]$Model = "auto",
    [string]$ModelsFrom,
    [switch]$SkipModel,
    [switch]$AcceptAgreements,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$ServerUrl = "http://127.0.0.1:11434"
$InstallerUrl = "https://ollama.com/download/OllamaSetup.exe"

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

function Find-Ollama {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $default) { return $default }
    return $null
}

function Test-Server {
    try { Invoke-RestMethod "$ServerUrl/api/version" -TimeoutSec 2 | Out-Null; return $true }
    catch { return $false }
}

# ---- 1. what this PC has -------------------------------------------------------------
$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
if ($Model -eq "auto") { $Model = if ($ramGB -ge 16) { "qwen2.5:7b" } else { "llama3.2:3b" } }
$modelsDir = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE ".ollama\models" }
$drive = Get-PSDrive -Name (Split-Path $env:USERPROFILE -Qualifier).TrimEnd(":")
$freeGB = [math]::Round($drive.Free / 1GB, 1)
$ollama = Find-Ollama

Say "RAM:            $ramGB GB"
Say "Free disk:      $freeGB GB on $($drive.Name):"
Say "Ollama:         $(if ($ollama) { $ollama } else { 'not installed' })"
Say "Chat model:     $(if ($SkipModel) { '(skipped)' } elseif ($ModelsFrom) { "copy from $ModelsFrom" } else { $Model })"
Say "Models folder:  $modelsDir"

if ($env:OLLAMA_HOST -and $env:OLLAMA_HOST -notmatch '^(https?://)?(127\.0\.0\.1|localhost)(:\d+)?/?$') {
    Say "Warning: OLLAMA_HOST is set to '$env:OLLAMA_HOST'. LexiVox only talks to Ollama on 127.0.0.1," Yellow
    Say "         and serving on other addresses exposes Ollama to your network. Consider removing it." Yellow
}
if (-not $SkipModel -and -not $ModelsFrom -and $freeGB -lt 7) {
    Say "Warning: under 7 GB free. The model download may not fit." Yellow
}
if ($DryRun) { Say "`nDry run: nothing was installed or downloaded." Cyan; exit 0 }

# ---- 2. install Ollama ---------------------------------------------------------------
if (-not $ollama) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "`nInstalling Ollama with winget..." Cyan
        $wingetArgs = @("install", "--id", "Ollama.Ollama", "--exact", "--silent")
        if ($AcceptAgreements) { $wingetArgs += @("--accept-package-agreements", "--accept-source-agreements") }
        & winget @wingetArgs
    } else {
        Say "`nwinget isn't available; downloading the official installer from ollama.com..." Cyan
        $setup = Join-Path $env:TEMP "OllamaSetup.exe"
        Invoke-WebRequest $InstallerUrl -OutFile $setup -UseBasicParsing
        $sig = Get-AuthenticodeSignature $setup
        if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "Ollama") {
            Remove-Item $setup -Force
            throw "The downloaded installer isn't validly signed by Ollama ($($sig.Status)). Stopped."
        }
        Start-Process $setup -ArgumentList "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES" -Wait
        Remove-Item $setup -Force
    }
    $ollama = Find-Ollama
    if (-not $ollama) { throw "Ollama didn't install. Install it from https://ollama.com/download and run this again." }
    $env:Path = (Split-Path $ollama) + ";" + $env:Path
    Say "Installed: $ollama" Green
}

# ---- 3. make sure the server is running ----------------------------------------------
if (-not (Test-Server)) {
    Say "`nStarting the Ollama server..." Cyan
    Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Server) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Server)) { throw "The Ollama server didn't start. Open the Ollama app once, then run this again." }
}
Say "Ollama server is running at $ServerUrl" Green

# ---- 4. add the chat model -----------------------------------------------------------
if ($ModelsFrom) {
    if (-not (Test-Path (Join-Path $ModelsFrom "manifests"))) {
        throw "$ModelsFrom doesn't look like an Ollama models folder (no 'manifests' inside)."
    }
    Say "`nCopying models from $ModelsFrom..." Cyan
    robocopy $ModelsFrom $modelsDir /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying the models failed (robocopy code $LASTEXITCODE)." }
} elseif (-not $SkipModel) {
    Say "`nDownloading $Model (this can take a while)..." Cyan
    & $ollama pull $Model
    if ($LASTEXITCODE -ne 0) { throw "Downloading $Model failed." }
}

# ---- 5. check it works ---------------------------------------------------------------
$names = @((Invoke-RestMethod "$ServerUrl/api/tags").models | ForEach-Object { $_.name })
$chat = @($names | Where-Object { $_ -notmatch "embed" })
if (-not $chat) {
    Say "`nOllama is installed, but there's no chat model yet. AI polish will paste text as spoken." Yellow
    exit 0
}
$testModel = if ($chat -contains $Model) { $Model } else { $chat[0] }
Say "`nTesting $testModel (the first run loads it into memory)..." Cyan
$body = @{ model = $testModel; prompt = "Reply with the single word: ready"; stream = $false; think = $false } | ConvertTo-Json
$timer = [Diagnostics.Stopwatch]::StartNew()
$reply = Invoke-RestMethod "$ServerUrl/api/generate" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 300
Say "Model answered '$($reply.response.Trim())' in $([math]::Round($timer.Elapsed.TotalSeconds, 1)) s" Green

Say "`nDone. Installed chat models: $($chat -join ', ')" Green
Say "LexiVox picks one automatically; choose another in Settings > AI polish." Green
