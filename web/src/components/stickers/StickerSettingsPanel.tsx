import { useId, useState } from 'react'
import { LoaderCircle } from 'lucide-react'

import { useAction } from '@/components/bots/shared'
import { ResultText, type Result } from '@/components/Feedback'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import { sendJson, type ProviderView, type StickerList, type StickerSettings } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import { parseNumber } from '@/lib/forms'

type Limit = { key: keyof StickerSettings; label: string; unit: string; min: number; max: number }

const LIMITS: Limit[] = [
  { key: 'max_count', label: '最多收藏', unit: '张', min: 10, max: 5000 },
  { key: 'max_mb', label: '最多占用', unit: 'MB', min: 5, max: 2000 },
  { key: 'describe_per_hour', label: '每小时最多描述', unit: '张', min: 1, max: 600 },
  { key: 'notes_per_hour', label: '每小时最多看聊天图片（0 是不看）', unit: '张', min: 0, max: 600 },
  { key: 'hourly_per_bot', label: '每个 bot 每群每小时最多发', unit: '张', min: 1, max: 60 },
  { key: 'group_gap', label: '同一个群两张表情至少隔', unit: '秒', min: 10, max: 3600 },
]

/** Which groups collect, the vision model (with 「测试看图」) and the limits. */
export function StickerSettingsPanel({
  data,
  providers,
  reload,
}: {
  data: StickerList
  providers: ProviderView[] | null
  reload: () => void
}) {
  const { busy, error, run } = useAction(reload)
  const [testing, setTesting] = useState(false)
  const [tested, setTested] = useState<Result | null>(null)
  const id = useId()
  const vision = data.settings.vision_provider ?? ''

  const toggle = (key: string, next: boolean) =>
    void run(() => sendJson('PATCH', `/api/groups/${encodeURIComponent(key)}/features`, { collect_stickers: next }))
  const choose = (next: string) => {
    setTested(null)
    void run(() => sendJson('PATCH', '/api/stickers/settings', { vision_provider: next || null }))
  }
  const test = async () => {
    if (!vision) return
    setTesting(true)
    setTested(null)
    try {
      const answer = await sendJson<{ ok: boolean; answer: string; error: string | null }>('POST', '/api/stickers/vision-test', {
        provider_id: vision,
      })
      setTested(
        answer.error
          ? { ok: false, text: `调用失败：${answer.error}` }
          : answer.ok
            ? { ok: true, text: `能看图。它说：${answer.answer}` }
            : { ok: false, text: `好像看不懂图片（测试图是一个红色的圆）。它说：${answer.answer || '（没说话）'}` },
      )
    } catch (e) {
      setTested({ ok: false, text: (e as Error).message })
    } finally {
      setTesting(false)
    }
  }

  return (
    <section className="space-y-5 rounded-2xl border bg-card p-5">
      <div className="space-y-2">
        <h2 className="text-sm font-semibold">在哪些群收藏</h2>
        <p className="text-xs text-muted-foreground">
          只收群友发的表情包和商城表情，不收普通照片和截图。所有群共用一个表情库。
        </p>
        <ul className="flex flex-wrap gap-x-6 gap-y-2">
          {data.groups.map((group) => (
            <li key={group.key}>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={group.collect} disabled={busy} onCheckedChange={(next) => toggle(group.key, next)} />
                {group.name || `群 ${group.key.split(':').pop()}`}
              </label>
            </li>
          ))}
          {data.groups.length === 0 && <li className="text-sm text-muted-foreground">bot 还没有在任何群里收到过消息</li>}
        </ul>
      </div>

      <div className="space-y-2">
        <Label htmlFor={`${id}-vision`} className="text-sm font-semibold">
          看图模型
        </Label>
        <p className="text-xs text-muted-foreground">
          给每张表情写一句描述，回答问题时也用它读截图上的字。要选能看图的模型，选好后点「测试看图」确认。
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect id={`${id}-vision`} value={vision} disabled={busy} onChange={(e) => choose(e.target.value)}>
            <option value="">不描述（表情只收着，不会被发出）</option>
            {(providers ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.id}（{p.model}）
              </option>
            ))}
          </NativeSelect>
          <Button size="sm" variant="outline" disabled={!vision || testing} onClick={() => void test()}>
            {testing && <LoaderCircle className="animate-spin" />}
            测试看图
          </Button>
        </div>
        {vision && !data.vision_loaded && (
          <p role="alert" className="text-xs text-destructive">
            模型 {vision} 没有加载，表情先排队，等它能用了再描述
          </p>
        )}
        <ResultText result={tested} />
      </div>

      <Limits key={JSON.stringify(data.settings)} settings={data.settings} busy={busy} run={run} />

      <p className="text-xs text-muted-foreground tabular-nums">
        现在收藏了 {data.count} 张，占 {formatBytes(data.bytes)}。超过上限时先删最久没见到、没发过的，「保留」的不会被删；
        「不适合」的 7 天后自动删掉。
      </p>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}

function Limits({
  settings,
  busy,
  run,
}: {
  settings: StickerSettings
  busy: boolean
  run: (action: () => Promise<unknown>) => Promise<boolean>
}) {
  const id = useId()
  const [values, setValues] = useState(() => Object.fromEntries(LIMITS.map((l) => [l.key, String(settings[l.key])])))
  const [problem, setProblem] = useState('')
  const dirty = LIMITS.some((l) => values[l.key] !== String(settings[l.key]))

  const save = () => {
    const body: Record<string, number> = {}
    for (const limit of LIMITS) {
      const parsed = parseNumber(values[limit.key], { min: limit.min, max: limit.max, integer: true })
      if (!parsed.ok || parsed.value === null) {
        setProblem(`${limit.label}要填 ${limit.min}–${limit.max} 之间的整数`)
        return
      }
      if (parsed.value !== settings[limit.key]) body[limit.key] = parsed.value
    }
    setProblem('')
    void run(() => sendJson('PATCH', '/api/stickers/settings', body))
  }

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold">上限</h2>
      <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {LIMITS.map((limit) => (
          <div key={limit.key} className="flex items-center gap-2">
            <Label htmlFor={`${id}-${limit.key}`} className="min-w-0 flex-1 text-xs font-normal text-muted-foreground">
              {limit.label}
            </Label>
            <Input
              id={`${id}-${limit.key}`}
              className="w-20"
              inputMode="numeric"
              autoComplete="off"
              value={values[limit.key]}
              onChange={(e) => setValues((v) => ({ ...v, [limit.key]: e.target.value }))}
            />
            <span className="w-8 text-xs text-muted-foreground">{limit.unit}</span>
          </div>
        ))}
      </div>
      {dirty && (
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={busy} onClick={save}>
            保存上限
          </Button>
          <span className="text-xs text-muted-foreground">调低张数或大小时会马上删掉超出的</span>
        </div>
      )}
      {problem && (
        <p role="alert" className="text-xs text-destructive">
          {problem}
        </p>
      )}
    </div>
  )
}
