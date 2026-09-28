<#
.SYNOPSIS
  Creates a LexiVox shortcut that runs LexiVox.bat, puts it in the Start menu, and
  tries to pin it to Start.

.DESCRIPTION
  The shortcut goes in Start > All apps (so it's also found by typing "LexiVox"), with
  the LexiVox icon and app ID, so notifications say "LexiVox". It starts minimized, so
  the .bat's console window doesn't flash up.

  Pinning: Windows 11 deliberately stops scripts from pinning apps to Start. The script
  tries anyway (it works on some Windows 10 builds); if Windows refuses, it says so and
  you pin it with one right-click: Start > All apps > LexiVox > Pin to Start.

.PARAMETER Desktop
  Also put a shortcut on the desktop.

.PARAMETER NoPin
  Don't try to pin to Start.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1 -Desktop
#>
param(
    [switch]$Desktop,
    [switch]$NoPin
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $root "LexiVox.bat"
if (-not (Test-Path $bat)) { throw "LexiVox.bat not found in $root" }

& (Join-Path $PSScriptRoot "make-shortcut.ps1") -Target $bat -Arguments "" -Minimized -Desktop:$Desktop

if ($NoPin) { exit 0 }

$lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "LexiVox.lnk"
$shell = New-Object -ComObject Shell.Application
$item = $shell.Namespace((Split-Path $lnk)).ParseName((Split-Path $lnk -Leaf))
$pin = $item.Verbs() | Where-Object { ($_.Name -replace "&", "") -match "^Pin to Start$" } | Select-Object -First 1
if ($pin) {
    $pin.DoIt()
    Write-Host "Pinned LexiVox to Start." -ForegroundColor Green
} else {
    Write-Host "LexiVox is in Start > All apps. Windows doesn't let scripts pin to Start," -ForegroundColor Yellow
    Write-Host "so to pin it: open Start > All apps, right-click LexiVox > Pin to Start." -ForegroundColor Yellow
}
