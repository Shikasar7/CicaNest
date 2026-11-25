import { useEffect, useState } from 'react'

import { DesktopUpdates } from '@/components/DesktopUpdates'
import { Switch } from '@/components/ui/switch'
import { desktop } from '@/lib/desktop'

/** What the desktop app itself offers (系统 page, and 设置 in the chat); nothing in a browser. */
export function DesktopOptions() {
  const [autostart, setAutostart] = useState<boolean | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    desktop?.getAutostart().then(setAutostart, () => setAutostart(null))
  }, [])
  if (!desktop) return null
  const toggle = async (next: boolean) => {
    setError('')
    setAutostart(next)
    try {
      await desktop!.setAutostart(next)
    } catch (e) {
      setAutostart(!next)
      setError((e as Error).message)
    }
  }
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <Switch checked={autostart ?? false} disabled={autostart === null} onCheckedChange={(v) => void toggle(v)} aria-label="开机时自动启动 nest" />
        <span className="text-sm">开机时自动启动 nest</span>
        <span className="text-xs text-muted-foreground">登录 Windows 后在后台打开，右下角会出现 nest 图标</span>
        {error && <p role="alert" className="w-full text-xs text-destructive">{error}</p>}
      </div>
      <DesktopUpdates />
    </div>
  )
}
