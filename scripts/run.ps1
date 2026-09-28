# Starts LexiVox from the project folder (the Start menu shortcut does the same).
$root = Split-Path -Parent $PSScriptRoot
Start-Process -FilePath (Join-Path $root ".venv\Scripts\pythonw.exe") -ArgumentList "`"$(Join-Path $root 'launcher.py')`"" -WorkingDirectory $root
