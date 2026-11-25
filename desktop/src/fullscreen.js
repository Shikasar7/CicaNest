'use strict'

// Whether a full-screen app (a game, a video) is in front, so the pets can step aside.
//
// Electron has no way to ask Windows for this, so a small PowerShell loop does: it looks at the window in
// front every few seconds and prints 1 when it covers its whole screen (the taskbar too), 0 when it does not,
// on every change. It reads no titles and no content, ends by itself when this app is gone, and runs only
// while there are pets on the desktop and the user wants this.

const { spawn } = require('node:child_process')
const log = require('./log')

const LOOK_SECONDS = 3
const RESTART_MS = 60_000

// The desktop and the taskbar cover screens too; a window of this app never counts.
const SCRIPT = `
$ErrorActionPreference = 'SilentlyContinue'
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class NestFront {
  [StructLayout(LayoutKind.Sequential)] public struct R { public int L, T, Rt, B; }
  [StructLayout(LayoutKind.Sequential)] public struct M { public int Size; public R All; public R Work; public uint Flags; }
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out R r);
  [DllImport("user32.dll")] static extern IntPtr MonitorFromWindow(IntPtr h, uint flags);
  [DllImport("user32.dll")] static extern bool GetMonitorInfo(IntPtr m, ref M info);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern bool IsZoomed(IntPtr h);
  [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
  public static void Start() { SetProcessDPIAware(); }
  public static bool Full(uint mine) {
    IntPtr h = GetForegroundWindow();
    if (h == IntPtr.Zero) return false;
    uint pid; GetWindowThreadProcessId(h, out pid);
    if (pid == mine) return false;
    StringBuilder name = new StringBuilder(64);
    GetClassName(h, name, 64);
    string c = name.ToString();
    if (c == "Progman" || c == "WorkerW" || c == "Shell_TrayWnd" || c == "XamlExplorerHostIslandWindow" || c == "MultitaskingViewFrame" || c == "ForegroundStaging") return false;
    R r; if (!GetWindowRect(h, out r)) return false;
    M info = new M(); info.Size = Marshal.SizeOf(typeof(M));
    if (!GetMonitorInfo(MonitorFromWindow(h, 2), ref info)) return false;
    bool covers = r.L <= info.All.L && r.T <= info.All.T && r.Rt >= info.All.Rt && r.B >= info.All.B;
    bool bar = info.Work.L != info.All.L || info.Work.T != info.All.T || info.Work.Rt != info.All.Rt || info.Work.B != info.All.B;
    return covers && (bar || !IsZoomed(h));
  }
}
'@
[NestFront]::Start()
$was = ''
while (Get-Process -Id __PID__) {
  $now = if ([NestFront]::Full(__PID__)) { '1' } else { '0' }
  if ($now -ne $was) { [Console]::Out.WriteLine($now); [Console]::Out.Flush(); $was = $now }
  Start-Sleep -Seconds ${LOOK_SECONDS}
}
`

class FullscreenWatch {
  /** onChange(full) when a full-screen app comes to the front or goes. */
  constructor(onChange) {
    this.onChange = onChange
    this.child = null
    this.full = false
    this.wanted = false
    this.timer = null
  }

  start() {
    this.wanted = true
    if (this.child || process.platform !== 'win32') return
    clearTimeout(this.timer)
    const script = SCRIPT.replaceAll('__PID__', String(process.pid))
    const encoded = Buffer.from(script, 'utf16le').toString('base64')
    const child = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], {
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore'],
    })
    this.child = child
    let rest = ''
    child.stdout.on('data', (chunk) => {
      rest += chunk.toString('utf8')
      const lines = rest.split(/\r?\n/)
      rest = lines.pop()
      for (const line of lines) if (line === '0' || line === '1') this.set(line === '1')
    })
    child.on('error', (err) => log.warn('fullscreen watch:', err.message))
    child.on('close', () => {
      if (this.child !== child) return
      this.child = null
      this.set(false)
      if (this.wanted) this.timer = setTimeout(() => this.wanted && this.start(), RESTART_MS)
    })
  }

  stop() {
    this.wanted = false
    clearTimeout(this.timer)
    const child = this.child
    this.child = null
    child?.kill()
    this.set(false)
  }

  set(full) {
    if (full === this.full) return
    this.full = full
    this.onChange(full)
  }
}

module.exports = { FullscreenWatch, SCRIPT }
