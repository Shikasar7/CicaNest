'use strict'

// What every command starts with: UTF-8 out, no progress
// records in it, and the PowerShell process put into a Windows job object of its own that ends every
// process in it when the job's last handle closes. The handle is held by that PowerShell process only, so
// when the command ends — normally, or ended at its time limit — what it started ends with it, including
// what it started with Start-Process to outlive it. If the job cannot be made (an old Windows, a locked
// down PowerShell), the command still runs and says so.

const PRELUDE = String.raw`[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ProgressPreference = 'SilentlyContinue'
$__nestJob = $false
try {
  Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NestCommandJob {
  [StructLayout(LayoutKind.Sequential)]
  struct Limits {
    public long PerProcessUserTimeLimit; public long PerJobUserTimeLimit; public uint LimitFlags;
    public UIntPtr MinimumWorkingSetSize; public UIntPtr MaximumWorkingSetSize; public uint ActiveProcessLimit;
    public UIntPtr Affinity; public uint PriorityClass; public uint SchedulingClass;
    public ulong ReadOperationCount; public ulong WriteOperationCount; public ulong OtherOperationCount;
    public ulong ReadTransferCount; public ulong WriteTransferCount; public ulong OtherTransferCount;
    public UIntPtr ProcessMemoryLimit; public UIntPtr JobMemoryLimit;
    public UIntPtr PeakProcessMemoryUsed; public UIntPtr PeakJobMemoryUsed;
  }
  [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref Limits info, int size);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
  [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
  static IntPtr held;
  public static bool Hold() {
    IntPtr job = CreateJobObject(IntPtr.Zero, null);
    if (job == IntPtr.Zero) return false;
    Limits info = new Limits();
    info.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (!SetInformationJobObject(job, 9, ref info, Marshal.SizeOf(typeof(Limits)))) return false;
    if (!AssignProcessToJobObject(job, GetCurrentProcess())) return false;
    held = job;
    return true;
  }
}
'@
  $__nestJob = [NestCommandJob]::Hold()
} catch {}
if (-not $__nestJob) { Write-Output '（提醒：没能把这条命令关进作业对象，它启动的其它程序在命令结束后可能还会继续运行）' }
Remove-Variable __nestJob
`

module.exports = { PRELUDE }
