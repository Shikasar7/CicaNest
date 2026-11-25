import { useEffect, useId, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import { getJson, sendJson, type ProviderView } from '@/lib/api'
import { useLive } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'

/** 主动性的设置 (nest.core.initiative): how often they come to you, when they leave you alone, and which model
 * writes the small talk and the day plans. */

type Values = {
  quiet_from: number
  quiet_until: number
  chatter_per_day: number
  chatter_per_hour: number
  outreach_per_bot: number
  outreach_daily: number
  outreach_hourly: number
  chatter_provider: string | null
  life_provider: string | null
  rhythm: boolean
}
type View = {
  values: Values
  ranges: Record<string, { default: number; min: number; max: number }>
  rhythm_days: number
  usual: { weekday: string; weekend: string }
}
type Numbers = 'chatter_per_day' | 'chatter_per_hour' | 'outreach_per_bot' | 'outreach_daily' | 'outreach_hourly'

const FIELDS: [Numbers, string, string][] = [
  ['outreach_per_bot', '每个伙伴一天最多主动找你', '次'],
  ['outreach_daily', '大家加起来一天最多', '次'],
  ['outreach_hourly', '一小时最多', '次'],
  ['chatter_per_day', '桌宠碎碎念，每个伙伴一天最多', '句'],
  ['chatter_per_hour', '碎碎念，大家一小时最多', '句'],
]

export function HomeSettings() {
  const { providersVersion } = useLive()
  const models = useLiveData<ProviderView[]>('/api/providers', [providersVersion]).data ?? []
  const [view, setView] = useState<View | null>(null)
  const [draft, setDraft] = useState<Partial<Record<Numbers, string>>>({})
  const [note, setNote] = useState('')
  const id = useId()
  useEffect(() => {
    getJson<View>('/api/life/settings').then(setView, (e: Error) => setNote(`读取失败：${e.message}`))
  }, [])
  const save = async (changes: Partial<Values>) => {
    setNote('')
    try {
      setView(await sendJson<View>('PUT', '/api/life/settings', changes))
      setDraft({})
      setNote('保存好了')
    } catch (e) {
      setNote((e as Error).message)
    }
  }
  if (!view) return note ? <p className="p-5 text-sm text-destructive">{note}</p> : null
  const hours = Array.from({ length: 24 }, (_, h) => h)
  const modelSelect = (name: 'chatter_provider' | 'life_provider', label: string) => (
    <label className="space-y-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <NativeSelect value={view.values[name] ?? ''} onChange={(e) => void save({ [name]: e.target.value || null })}>
        <option value="">用她们各自的模型</option>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.id}（{m.model}）
          </option>
        ))}
      </NativeSelect>
    </label>
  )
  return (
    <div className="space-y-5 rounded-3xl border bg-muted/20 p-5 text-sm">
      <section className="space-y-2">
        <h3 className="font-semibold">免打扰</h3>
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect aria-label="从几点" value={view.values.quiet_from} onChange={(e) => void save({ quiet_from: Number(e.target.value) })}>
            {hours.map((h) => (
              <option key={h} value={h}>
                {h} 点
              </option>
            ))}
          </NativeSelect>
          到
          <NativeSelect aria-label="到几点" value={view.values.quiet_until} onChange={(e) => void save({ quiet_until: Number(e.target.value) })}>
            {hours.map((h) => (
              <option key={h} value={h}>
                {h} 点
              </option>
            ))}
          </NativeSelect>
          <span className="text-xs text-muted-foreground">不主动说话（「这么晚还不睡」例外）。两头一样就是不设。</span>
        </div>
      </section>
      <section className="space-y-2">
        <h3 className="font-semibold">多久来一次</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {FIELDS.map(([name, label, unit]) => (
            <div key={name} className="space-y-1">
              <Label htmlFor={`${id}-${name}`} className="text-muted-foreground">
                {label}
              </Label>
              <div className="flex items-center gap-2">
                <Input
                  id={`${id}-${name}`}
                  type="number"
                  min={view.ranges[name].min}
                  max={view.ranges[name].max}
                  value={draft[name] ?? String(view.values[name])}
                  onChange={(e) => setDraft((d) => ({ ...d, [name]: e.target.value }))}
                  className="w-24"
                />
                <span className="text-xs text-muted-foreground">
                  {unit}（默认 {view.ranges[name].default}，0 是不来）
                </span>
              </div>
            </div>
          ))}
        </div>
        {Object.keys(draft).length > 0 && (
          <div className="flex gap-2">
            <Button
              size="sm"
              onClick={() => void save(Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, Number(v)])))}
            >
              保存
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDraft({})}>
              还原
            </Button>
          </div>
        )}
      </section>
      <section className="space-y-2">
        <h3 className="font-semibold">用哪个模型</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {modelSelect('chatter_provider', '碎碎念和主动找你')}
          {modelSelect('life_provider', '排日程')}
        </div>
        <p className="text-xs text-muted-foreground">换成便宜的模型能省一点。它用不了的时候会用回她自己的模型。</p>
      </section>
      <section className="space-y-2">
        <h3 className="font-semibold">你的作息</h3>
        <label className="flex items-center gap-2">
          <Switch checked={view.values.rhythm} onCheckedChange={(rhythm) => void save({ rhythm })} />
          记住你平时几点在电脑前
        </label>
        <p className="text-xs text-muted-foreground">
          只记每小时在电脑前几分钟，存在 nest 里，最多四周；她们用来知道现在对你算不算晚。
          {view.rhythm_days > 0 ? `已经记了 ${view.rhythm_days} 天。` : ''}
          {view.usual.weekday && `工作日：${view.usual.weekday}`}
          {view.usual.weekend && `周末：${view.usual.weekend}`}
        </p>
        {view.rhythm_days > 0 && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              void sendJson<View>('DELETE', '/api/life/settings/rhythm').then(setView, (e: Error) => setNote(e.message))
            }
          >
            忘掉记下的
          </Button>
        )}
      </section>
      {note && (
        <p role="status" className="text-xs text-muted-foreground">
          {note}
        </p>
      )}
    </div>
  )
}
