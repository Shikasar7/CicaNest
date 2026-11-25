import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { desktop, type DesktopUpdate } from '@/lib/desktop'

const mb = (bytes: number) => `${Math.max(1, Math.round(bytes / 1024 / 1024))} MB`

function line(update: DesktopUpdate): string {
  switch (update.state) {
    case 'checking':
      return '正在检查更新…'
    case 'latest':
      return '已经是最新的'
    case 'available':
      return `有新版本 ${update.version}${update.size ? `（${mb(update.size)}）` : ''}`
    case 'downloading':
      return `正在下载 ${update.version}：${update.percent}%`
    case 'ready':
      return `${update.version} 下载好了，可以安装`
    case 'installing':
      return `正在更新到 ${update.version}…`
    default:
      return update.version ? `有新版本 ${update.version}` : ''
  }
}

/** 更新, in what the desktop app offers; nothing on an app that cannot be updated, or is from before updates. */
export function DesktopUpdates() {
  const [update, setUpdate] = useState<DesktopUpdate | null>(null)
  const [failed, setFailed] = useState('')
  useEffect(() => {
    if (!desktop?.getUpdate) return
    let on = true
    const read = () => void desktop!.getUpdate!().then((u) => on && setUpdate(u), () => {})
    read()
    const off = desktop.onUpdate?.(read)
    return () => {
      on = false
      off?.()
    }
  }, [])
  if (!update) return null

  const run = async (action?: () => Promise<DesktopUpdate | null>) => {
    if (!action) return
    setFailed('')
    try {
      const after = await action()
      if (after) setUpdate(after)
    } catch (e) {
      setFailed((e as Error).message)
    }
  }
  const busy = update.state === 'checking' || update.state === 'downloading' || update.state === 'installing'
  const installable = update.how !== 'page'
  const error = failed || update.error
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <p className="text-sm" role="status">
          nest {update.current}
          {line(update) && <span className="text-muted-foreground"> · {line(update)}</span>}
        </p>
        {update.state === 'ready' ? (
          <Button size="sm" onClick={() => void run(desktop?.installUpdate)}>
            安装…
          </Button>
        ) : update.state === 'available' && installable ? (
          <Button size="sm" onClick={() => void run(desktop?.downloadUpdate)}>
            更新
          </Button>
        ) : (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(desktop?.checkUpdate)}>
            检查更新
          </Button>
        )}
        {update.page && (
          <Button size="sm" variant="link" onClick={() => void desktop?.openUpdatePage?.()}>
            打开发布页
          </Button>
        )}
      </div>
      {update.state === 'downloading' && (
        <div
          role="progressbar"
          aria-label="下载进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={update.percent}
          className="h-1.5 max-w-md overflow-hidden rounded-full bg-muted"
        >
          <div className="h-full bg-primary transition-[width]" style={{ width: `${update.percent}%` }} />
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {update.notes && (
        <div
          tabIndex={0}
          role="region"
          aria-label={`${update.version} 的更新说明`}
          className="max-h-56 max-w-2xl overflow-y-auto rounded-lg border p-3 text-xs leading-relaxed whitespace-pre-line text-muted-foreground"
        >
          {update.notes}
        </div>
      )}
      {(update.state === 'available' || update.state === 'ready') && (
        <p className="text-xs text-muted-foreground">
          {!installable
            ? '这一份不是安装包装的，到发布页下载安装包。'
            : desktop?.remote
              ? '下载好后会再问一次。安装时聊天窗口和桌宠会关一两分钟，数据不变。'
              : '下载好后会再问一次。安装时所有 bot 会停一两分钟，数据不变。'}
        </p>
      )}
      {desktop?.remote && (
        <p className="text-xs text-muted-foreground">
          这里更新的只是这台电脑上的桌面端。那台电脑上的 nest{update.otherVersion ? ` 是 ${update.otherVersion}` : ''}，要在那台上更新。
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Switch
          checked={update.auto}
          onCheckedChange={(on) => void run(() => desktop!.setUpdateAuto!(on))}
          aria-label="自动检查更新"
        />
        <span className="text-sm">自动检查更新</span>
        <span className="text-xs text-muted-foreground">每 12 小时问一次 GitHub，有新版本只提醒，不会自己装</span>
      </div>
    </div>
  )
}
