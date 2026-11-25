import { useCallback, useEffect, useId, useState } from 'react'
import { Check, LoaderCircle, Plus } from 'lucide-react'

import { ModelSelect } from '@/components/chat/ModelSelect'
import { Label } from '@/components/ui/label'
import { chatView, openConsole } from '@/lib/desktop'
import { getJson, sendJson } from '@/lib/api'
import { useProviders } from '@/lib/live'
import { usable } from '@/lib/models'

type Sample = {
  id: string
  name: string
  description: string
  persona: string
  extras: string[]
  /** The companion already made from it. */
  bot_id: string | null
}

const EXTRA: Record<string, string> = { pets: '桌宠', stickers: '表情包', skills: '技能' }

/** 示例伙伴: companions that come with nest, to try it with. Nobody is here until you take one in; from then on
 * she is yours to change like any other. */
export function Samples({ onDone }: { onDone: (botId: string) => void }) {
  const { data: providers } = useProviders()
  const models = usable(providers)
  const [samples, setSamples] = useState<Sample[] | null>(null)
  const [provider, setProvider] = useState('')
  const [busy, setBusy] = useState('')
  const [problem, setProblem] = useState('')
  const model = useId()
  const chosen = provider || models[0]?.id || ''

  const read = useCallback(
    () => getJson<Sample[]>('/api/samples').then(setSamples, (e: Error) => setProblem(`读取失败：${e.message}`)),
    [],
  )
  useEffect(() => void read(), [read])

  const take = async (sample: Sample) => {
    setBusy(sample.id)
    setProblem('')
    try {
      const made = await sendJson<{ bot_id: string }>('POST', `/api/samples/${encodeURIComponent(sample.id)}`, {
        provider_id: chosen || null,
      })
      onDone(made.bot_id)
    } catch (e) {
      setProblem((e as Error).message)
      void read()
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="space-y-5 text-sm">
      {providers && models.length === 0 ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl bg-muted/60 px-4 py-3">
          <p className="min-w-0 flex-1 text-muted-foreground">
            还没有模型，她来了也说不了话。先去控制台的「模型」页加一个，再回来。
          </p>
          <button
            type="button"
            onClick={() => (chatView ? openConsole() : window.location.assign('#/models'))}
            className="shrink-0 rounded-full border bg-card px-4 py-1.5 hover:bg-muted"
          >
            去加模型
          </button>
        </div>
      ) : (
        <div className="max-w-xs space-y-1">
          <Label htmlFor={model}>用哪个模型</Label>
          <ModelSelect id={model} value={chosen} onChange={setProvider} providers={models} />
        </div>
      )}
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(11.5rem,1fr))] gap-3">
        {samples?.map((sample) => (
          <li key={sample.id} className="flex flex-col items-center gap-2 rounded-3xl border bg-card p-4 text-center">
            <img
              src={`/api/samples/${encodeURIComponent(sample.id)}/avatar`}
              alt=""
              className="size-20 rounded-full object-cover"
            />
            <p className="text-base font-semibold">{sample.name}</p>
            <p className="flex-1 text-xs text-muted-foreground">{sample.description}</p>
            {sample.extras.length > 0 && (
              <p className="text-[11px] text-muted-foreground">
                带着{sample.extras.map((k) => EXTRA[k] ?? k).join('、')}
              </p>
            )}
            {sample.bot_id ? (
              <button
                type="button"
                onClick={() => onDone(sample.bot_id!)}
                className="flex w-full items-center justify-center gap-1.5 rounded-full border px-4 py-2 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Check className="size-4" />
                已经在了，去聊天
              </button>
            ) : (
              <button
                type="button"
                disabled={busy !== '' || !samples}
                onClick={() => void take(sample)}
                className="flex w-full items-center justify-center gap-1.5 rounded-full bg-primary px-4 py-2 font-medium text-primary-foreground transition-transform active:scale-[0.98] disabled:opacity-50"
              >
                {busy === sample.id ? <LoaderCircle className="size-4 animate-spin" /> : <Plus className="size-4" />}
                请{sample.name}过来
              </button>
            )}
          </li>
        ))}
      </ul>
      {problem && (
        <p role="alert" className="text-destructive">
          {problem}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        请过来以后就是你的伙伴了：人设、名字、头像都能改，不想要了在控制台的 Bot 页删掉。
      </p>
    </div>
  )
}

/** The chat with nobody in it yet. */
export function Welcome({ onDone, onNew }: { onDone: (botId: string) => void; onNew: () => void }) {
  return (
    <div className="min-h-0 min-w-0 flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-2xl space-y-6 py-4">
        <div className="space-y-2">
          <h2 className="text-xl font-semibold">先请谁过来？</h2>
          <p className="text-sm text-muted-foreground">
            这里还没有伙伴。可以从示例伙伴开始，也可以自己写一个，或者从别处搬一个进来。
          </p>
        </div>
        <Samples onDone={onDone} />
        <div className="border-t pt-5">
          <button
            type="button"
            onClick={onNew}
            className="rounded-full border px-4 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            自己写一个 / 搬一个进来
          </button>
        </div>
      </div>
    </div>
  )
}
