<#
.SYNOPSIS
  Creates a Start menu (and optionally Desktop) shortcut for LexiVox.

.DESCRIPTION
  The shortcut runs launcher.py with the project's pythonw.exe (no console window) and
  carries the app's AppUserModelID, which is what makes Windows show the name
  "LexiVox" and its icon on notifications instead of "Python".
  The app runs this once automatically if the Start menu shortcut is missing.
  -Target/-Arguments point it somewhere else (create-shortcut.ps1 uses LexiVox.bat).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\make-shortcut.ps1 -Desktop
#>
param(
    [switch]$Desktop,
    [string]$Target,
    [string]$Arguments,
    [switch]$Minimized,
    [string]$AppId = "LexiVox.App",
    [string]$Name = "LexiVox"
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
if (-not $Target) {
    $Target = Join-Path $root ".venv\Scripts\pythonw.exe"
    if (-not $PSBoundParameters.ContainsKey("Arguments")) { $Arguments = "`"$(Join-Path $root 'launcher.py')`"" }
}
if (-not (Test-Path $Target)) { throw "Not found: $Target. Run scripts\install.ps1 first." }
# The repo ships the icon, so shortcuts look right even before the app's first run.
$icon = Join-Path $root "assets\brand\lexivox.ico"
if (-not (Test-Path $icon)) { $icon = Join-Path $env:LOCALAPPDATA "LexiVox\icon\app.ico" }
$showCmd = if ($Minimized) { 7 } else { 1 }  # 7 = start minimized: hides a .bat's console flash

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

[ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder f, int c, IntPtr fd, uint fl);
    void GetIDList(out IntPtr p); void SetIDList(IntPtr p);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string s);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string s);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string s);
    void GetHotkey(out short h); void SetHotkey(short h);
    void GetShowCmd(out int i); void SetShowCmd(int i);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c, out int i);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string s, int i);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string s, uint r);
    void Resolve(IntPtr h, uint f);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string s);
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
public struct LxPropertyKey { public Guid fmtid; public uint pid; }

[StructLayout(LayoutKind.Explicit, Size = 16)]
public struct LxPropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
    void GetCount(out uint c);
    void GetAt(uint i, out LxPropertyKey k);
    void GetValue(ref LxPropertyKey k, out LxPropVariant v);
    void SetValue(ref LxPropertyKey k, ref LxPropVariant v);
    void Commit();
}

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
class LxShellLink {}

public static class LxShortcut {
    public static void Create(string path, string target, string args, string dir, string icon, string appId, string description, int showCmd) {
        var link = (IShellLinkW)new LxShellLink();
        link.SetPath(target);
        link.SetArguments(args);
        link.SetWorkingDirectory(dir);
        link.SetDescription(description);
        link.SetShowCmd(showCmd);
        if (System.IO.File.Exists(icon)) link.SetIconLocation(icon, 0);
        // PKEY_AppUserModel_ID
        var key = new LxPropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
        var value = new LxPropVariant { vt = 31, p = Marshal.StringToCoTaskMemUni(appId) };  // VT_LPWSTR
        try {
            var store = (IPropertyStore)link;
            store.SetValue(ref key, ref value);
            store.Commit();
        } finally {
            Marshal.FreeCoTaskMem(value.p);
        }
        ((IPersistFile)link).Save(path, true);
    }
}
"@

$programs = [Environment]::GetFolderPath("Programs")
$paths = @(Join-Path $programs "$Name.lnk")
if ($Desktop) { $paths += Join-Path ([Environment]::GetFolderPath("Desktop")) "$Name.lnk" }
foreach ($p in $paths) {
    [LxShortcut]::Create($p, $Target, "$Arguments", $root, $icon, $AppId, "Private, offline dictation and transcription", $showCmd)
    Write-Output "Created $p"
}
