import { useId, useState } from 'react'

import { groupName, useAction } from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { sendJson, type Bot, type Ok, type Persona, type PersonaSet } from '@/lib/api'
import { cn } from '@/lib/utils'

const NAME_MAX = 20

/** Naming a new set, or renaming one. */
type Naming = { id: number | null; name: string }

/**
 * 人设方案: the persona this bot uses in each group and private chat, saved under a name, so the whole set can
 * be switched in one click and switched back. Places added after a set was saved keep their persona.
 */
export function PersonaSets({ bot, personas, reload }: { bot: Bot; personas: Persona[] | null; reload: () => void }) {
  const sets = bot.persona_sets ?? []
  const base = `/api/bots/${encodeURIComponent(bot.id)}/persona-sets`
  const { busy, error, setError, run } = useAction(reload)
  const [naming, setNaming] = useState<Naming | null>(null)
  const [done, setDone] = useState('')
  const ids = { name: useId(), error: useId() }
  const saved = sets.some((s) => s.current)

  const personaName = (id: number | null) =>
    id === null ? '不用人设' : (personas?.find((p) => p.id === id)?.name ?? bot.channels.find((c) => c.persona_id === id)?.persona_name ?? '人设已不在列表里')
  const placeName = (channelId: number) => {
    const channel = bot.channels.find((c) => c.channel_id === channelId)
    return channel ? groupName(channel) : ''
  }

  const startNaming = (next: Naming) => {
    setNaming(next)
    setError('')
    setDone('')
  }

  const submitName = async () => {
    if (!naming) return
    const name = naming.name.trim()
    if (!name) {
      setError('给方案起个名字')
      return
    }
    if ([...name].length > NAME_MAX) {
      setError(`方案名字最多 ${NAME_MAX} 个字`)
      return
    }
    const ok =
      naming.id === null
        ? await run(() => sendJson('POST', base, { name }))
        : await run(() => sendJson<Ok>('PATCH', `${base}/${naming.id}`, { name }))
    if (ok) {
      setNaming(null)
      setDone(naming.id === null ? `存好了：「${name}」` : '')
    }
  }

  const apply = async (set: PersonaSet) => {
    if (
      !saved &&
      !window.confirm(`${bot.name}现在的人设搭配还没存成方案，换成「${set.name}」后就要一个个手动改回来。\n\n仍然切换？`)
    ) {
      return
    }
    setDone('')
    let changed = 0
    const ok = await run(async () => {
      changed = (await sendJson<{ changed: number }>('POST', `${base}/${set.id}/apply`)).changed
    })
    if (ok) setDone(changed ? `已换成「${set.name}」，改了 ${changed} 个地方` : `已经是「${set.name}」了`)
  }

  const resave = async (set: PersonaSet) => {
    if (!window.confirm(`用${bot.name}现在每个地方的人设覆盖方案「${set.name}」？`)) return
    setDone('')
    if (await run(() => sendJson<Ok>('PATCH', `${base}/${set.id}`, { resave: true }))) setDone(`「${set.name}」已更新为现在的搭配`)
  }

  const remove = async (set: PersonaSet) => {
    if (!window.confirm(`删掉方案「${set.name}」？里面用到的人设还留在人设库里。`)) return
    setDone('')
    await run(() => sendJson<Ok>('DELETE', `${base}/${set.id}`))
  }

  const nameForm = naming && (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        void submitName()
      }}
      onKeyDown={(e) => e.key === 'Escape' && setNaming(null)}
    >
      <div className="min-w-40 flex-1 space-y-1">
        <Label htmlFor={ids.name}>{naming.id === null ? '新方案的名字' : '改名为'}</Label>
        <Input
          id={ids.name}
          autoFocus
          value={naming.name}
          maxLength={NAME_MAX * 2}
          placeholder="比如：原来的、圣诞节"
          aria-invalid={Boolean(error)}
          aria-describedby={error ? ids.error : undefined}
          onChange={(e) => setNaming({ ...naming, name: e.target.value })}
        />
      </div>
      <Button type="submit" size="sm" disabled={busy}>
        {naming.id === null ? '保存' : '改名'}
      </Button>
      <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setNaming(null)}>
        取消
      </Button>
    </form>
  )

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        把{bot.name}现在每个群、私聊用的人设存成一个方案，以后点一下就整套换过去，再点一下换回来。名字、记忆不跟着换。
      </p>
      {sets.length > 0 && (
        <ul className="space-y-2">
          {sets.map((set) => (
            <li key={set.id} className={cn('space-y-2 rounded-lg border px-3 py-2.5', set.current && 'border-[var(--hue)]/50 bg-[var(--hue)]/5')}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium break-all">{set.name}</span>
                {set.current && <span className="rounded-full bg-[var(--hue)]/15 px-2 py-0.5 text-xs text-[var(--hue)]">正在用</span>}
                <div className="ml-auto flex flex-wrap gap-1">
                  <Button size="sm" variant={set.current ? 'ghost' : 'outline'} disabled={busy || set.current} onClick={() => void apply(set)}>
                    换成这套
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => void resave(set)}>
                    用现在的覆盖
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => startNaming({ id: set.id, name: set.name })}>
                    改名
                  </Button>
                  <Button size="sm" variant="ghost" className="text-destructive" disabled={busy} onClick={() => void remove(set)}>
                    删除
                  </Button>
                </div>
              </div>
              {naming?.id === set.id && nameForm}
              {set.items.length === 0 ? (
                <p className="text-xs text-muted-foreground">存的地方{bot.name}都已经不在了，换过去不会改任何东西</p>
              ) : (
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
                  {set.items.map((item) => (
                    <div key={item.channel_id} className="contents">
                      <dt className="text-muted-foreground">{placeName(item.channel_id)}</dt>
                      <dd className="break-all">{personaName(item.persona_id)}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </li>
          ))}
        </ul>
      )}
      {naming?.id === null ? (
        nameForm
      ) : (
        <Button size="sm" variant="outline" disabled={busy || bot.channels.length === 0} onClick={() => startNaming({ id: null, name: '' })}>
          把现在的搭配存成方案
        </Button>
      )}
      {bot.channels.length === 0 && <p className="text-xs text-muted-foreground">还没有群或私聊，没有人设可存</p>}
      {error && (
        <p id={ids.error} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {done && !error && (
        <p role="status" className="text-xs text-ok">
          {done}
        </p>
      )}
    </div>
  )
}
