const pad = (n: number) => String(n).padStart(2, '0')

/** "14:03:21" for today, "10-25 14:03" otherwise. Input is a unix timestamp in seconds. */
export function formatTime(seconds: number): string {
  const date = new Date(seconds * 1000)
  const now = new Date()
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  if (date.toDateString() === now.toDateString()) return `${clock}:${pad(date.getSeconds())}`
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${clock}`
}

/** "刚刚" / "3 分钟前" / "2 小时前" / "10-25 14:03". */
export function formatAgo(seconds: number): string {
  const delta = Date.now() / 1000 - seconds
  if (delta < 45) return '刚刚'
  if (delta < 3600) return `${Math.round(delta / 60)} 分钟前`
  if (delta < 86400) return `${Math.round(delta / 3600)} 小时前`
  return formatTime(seconds)
}

export function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  const hours = Math.floor(seconds / 3600)
  return hours < 48 ? `${hours} 小时 ${Math.floor((seconds % 3600) / 60)} 分钟` : `${Math.floor(hours / 24)} 天`
}

/** "820 KB", "4.5 MB", "1.2 GB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

/** "820 毫秒" below a second, "3.7 秒" above; "—" when unknown. */
export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '—'
  if (ms < 999.5) return `${Math.round(ms)} 毫秒`
  return `${(ms / 1000).toFixed(1)} 秒`
}

/** "9,876" below ten thousand, then "1.2万" / "3.4亿". */
export function formatCount(n: number): string {
  const compact = (value: number, unit: string) => `${Number(value.toFixed(1))}${unit}`
  const wan = Number((n / 1e4).toFixed(1)) // round first: 99,999,600 is "1亿", not "10000万"
  if (n >= 1e8 || wan >= 1e4) return compact(n / 1e8, '亿')
  if (n >= 1e4) return compact(n / 1e4, '万')
  return n.toLocaleString('zh-CN')
}

/** A 0–1 rate as "3.2%"; "—" when unknown. */
export function formatRate(rate: number | null): string {
  if (rate === null || !Number.isFinite(rate)) return '—'
  if (rate > 0 && rate < 0.001) return '<0.1%'
  if (rate < 1 && rate >= 0.9995) return '>99.9%' // not every call failed, so never "100%"
  return `${Number((rate * 100).toFixed(1))}%`
}

/** "10-25" for a local "2026-10-25". */
export const shortDay = (day: string) => day.slice(5)

/** "iPhone 上的 Safari", "Windows 上的 Edge" from a browser's User-Agent; "未知设备" when it says nothing useful. */
export function describeAgent(agent: string): string {
  if (/\bElectron\//.test(agent)) return 'nest 桌面应用'
  const os = /iPhone/.test(agent)
    ? 'iPhone'
    : /iPad/.test(agent)
      ? 'iPad'
      : /Android/.test(agent)
        ? 'Android'
        : /Macintosh|Mac OS X/.test(agent)
          ? 'Mac'
          : /Windows/.test(agent)
            ? 'Windows'
            : /Linux/.test(agent)
              ? 'Linux'
              : ''
  const browser = /\bEdg(A|iOS)?\//.test(agent)
    ? 'Edge'
    : /\bOPR\//.test(agent)
      ? 'Opera'
      : /\b(Firefox|FxiOS)\//.test(agent)
        ? 'Firefox'
        : /\b(Chrome|CriOS)\//.test(agent)
          ? 'Chrome'
          : /\bSafari\//.test(agent)
            ? 'Safari'
            : ''
  if (os && browser) return `${os} 上的 ${browser}`
  return os || browser || '未知设备'
}
