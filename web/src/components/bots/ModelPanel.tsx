import { useState } from 'react'

import { ResultText, type Result } from '@/components/Feedback'
import { ModelHealthBadge } from '@/components/ModelHealthBadge'
import { Button } from '@/components/ui/button'
import { sendJson, type Bot, type ProviderTestResult } from '@/lib/api'
import { formatAgo, formatLatency } from '@/lib/format'
import { useLive } from '@/lib/live'

/** The bot's model: how it is doing, and 测试一下. */
export function ModelPanel({ bot }: { bot: Bot }) {
  const { refreshConfig } = useLive()
  const [result, setResult] = useState<Result | null>(null)
  const [testing, setTesting] = useState(false)
  const provider = bot.provider
  if (!provider) {
    return (
      <p className="text-sm text-destructive">
        {bot.provider_id ? `模型 ${bot.provider_id} 配置有误，去「模型」页修改` : '还没有配置模型'}
      </p>
    )
  }
  const test = async () => {
    setTesting(true)
    setResult(null)
    try {
      const r = await sendJson<ProviderTestResult>('POST', `/api/providers/${encodeURIComponent(provider.id)}/test`)
      setResult(
        r.ok
          ? { ok: true, text: `回复「${r.text}」，用时 ${formatLatency(r.latency_ms)}` }
          : { ok: false, text: `失败：${r.error}` },
      )
    } catch (e) {
      setResult({ ok: false, text: `失败：${(e as Error).message}` })
    } finally {
      setTesting(false)
      refreshConfig() // the test updates health but publishes no event
    }
  }
  return (
    <div className="space-y-1 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{provider.id}</span>
        <span className="text-muted-foreground">{provider.model}</span>
        <ModelHealthBadge health={provider} />
        <Button size="sm" variant="outline" disabled={testing} onClick={() => void test()}>
          {testing ? '测试中…' : '测试一下'}
        </Button>
      </div>
      <ResultText result={result} />
      {!result && provider.last_error && (
        <p className="text-xs break-words text-muted-foreground">最近一次失败：{provider.last_error}</p>
      )}
      {!result && provider.last_ok_at && (
        <p className="text-xs text-muted-foreground">最近一次成功：{formatAgo(provider.last_ok_at)}</p>
      )}
    </div>
  )
}
