import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { getJson, type QQLoginView } from '@/lib/api'
import { cn } from '@/lib/utils'

/** A NapCat container's login QR code, read through nest: its own page is only reachable on the Mac, the
 * console from the phone too. Asks again every 3 seconds, so a new code replaces an expired one, until the
 * account is connected. */
export function QRLogin({ container, onClose }: { container: string; onClose: () => void }) {
  const [view, setView] = useState<QQLoginView | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    const ask = () => {
      getJson<QQLoginView>(`/api/system/qq/login?container=${encodeURIComponent(container)}`).then(
        (v) => {
          if (stopped) return
          setView(v)
          setError('')
          if (!v.logged_in) timer = window.setTimeout(ask, 3000)
        },
        (e) => {
          if (stopped) return
          setError((e as Error).message)
          timer = window.setTimeout(ask, 6000)
        },
      )
    }
    ask()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [container])
  return (
    <div className="flex flex-wrap items-start gap-4 rounded-xl border bg-muted/40 p-4">
      {view?.qr && (
        <img
          src={view.qr}
          alt={`${view.name || container} 的登录二维码`}
          width={176}
          height={176}
          className="rounded-lg bg-white p-2 [image-rendering:pixelated]"
        />
      )}
      <div className="min-w-0 flex-1 space-y-2 text-sm">
        <p className={cn(view?.logged_in ? 'font-medium text-foreground' : 'text-muted-foreground')} aria-live="polite">
          {error || (view ? view.detail : '正在读取二维码…')}
        </p>
        {view?.qr && <p className="text-xs text-muted-foreground">在手机 QQ 里点右上角的「+」，选「扫一扫」。</p>}
        <Button size="sm" variant="outline" onClick={onClose}>
          {view?.logged_in ? '完成' : '收起'}
        </Button>
      </div>
    </div>
  )
}
