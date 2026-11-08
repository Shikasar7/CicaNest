import { useId, useState } from 'react'

import { ACTIVITY, useAction } from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { getJson, sendJson, type Activity, type Bot, type BotGroup, type BotGroups, type ChannelCreate, type Persona } from '@/lib/api'
import { can } from '@/lib/platforms'

const groupLabel = (group: BotGroup) => (group.name ? `${group.name}（${group.group_id}）` : `群 ${group.group_id}`)

/** 在其他群启用: a group the bot's account is in, where the bot does not take part yet. */
export function AddGroup({ bot, personas, reload }: { bot: Bot; personas: Persona[] | null; reload: () => void }) {
  const [open, setOpen] = useState(false)
  const [groups, setGroups] = useState<BotGroups | null>(null)
  const [loadError, setLoadError] = useState('')
  const [channelId, setChannelId] = useState('')
  const [activity, setActivity] = useState<Activity>('mentions') // the quiet start for a new group
  const [personaId, setPersonaId] = useState('')
  const { busy, error, setError, run } = useAction(reload)
  const ids = { group: useId(), activity: useId(), persona: useId(), error: useId() }

  const load = () => {
    setLoadError('')
    getJson<BotGroups>(`/api/bots/${encodeURIComponent(bot.id)}/groups`).then(
      (value) => {
        setGroups(value)
        const first = value.groups.find((g) => !g.joined)
        setChannelId(first ? String(first.channel_id) : '')
      },
      (e: Error) => setLoadError(e.message),
    )
  }
  const start = () => {
    setOpen(true)
    setGroups(null)
    setActivity('mentions')
    setPersonaId('')
    setError('')
    load()
  }
  const close = () => setOpen(false)

  const candidates = groups?.groups.filter((g) => !g.joined) ?? []
  const chosen = candidates.find((g) => String(g.channel_id) === channelId)

  const add = async () => {
    if (!chosen) return
    const level = ACTIVITY.find((a) => a.value === activity)?.label
    if (bot.mode === 'live' && !window.confirm(`启用后 ${bot.name} 会在 ${groupLabel(chosen)} 真的发言（${level}）。继续？`)) {
      return
    }
    const body: ChannelCreate = { channel_id: chosen.channel_id, activity }
    if (personaId) body.persona_id = Number(personaId)
    if (await run(() => sendJson('POST', `/api/bots/${encodeURIComponent(bot.id)}/channels`, body))) close()
  }

  if (!open) {
    return (
      <Button size="sm" variant="outline" onClick={start}>
        在其他群启用
      </Button>
    )
  }
  return (
    <div
      role="group"
      aria-label="在其他群启用"
      className="space-y-3 rounded-md border p-3"
      onKeyDown={(e) => e.key === 'Escape' && close()}
    >
      {loadError ? (
        <p role="alert" className="text-sm text-destructive">
          读取群列表失败：{loadError}{' '}
          <button type="button" className="underline underline-offset-2" onClick={load}>
            再试一次
          </button>
        </p>
      ) : groups === null ? (
        <p role="status" className="text-sm text-muted-foreground">
          正在读取这个号所在的群…
        </p>
      ) : candidates.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {can(bot, 'list_channels')
            ? '这个号所在的群都已经启用了。'
            : '这个平台不给群列表：先去群里 @ 它一次，群才会出现在这里。'}
        </p>
      ) : (
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor={ids.group}>群</Label>
            <NativeSelect id={ids.group} value={channelId} onChange={(e) => setChannelId(e.target.value)} disabled={busy}>
              {candidates.map((g) => (
                <option key={g.channel_id} value={g.channel_id}>
                  {groupLabel(g)}
                </option>
              ))}
            </NativeSelect>
          </div>
          {can(bot, 'sees_all') && (
            <div className="space-y-1">
              <Label htmlFor={ids.activity}>活跃度</Label>
              <NativeSelect
                id={ids.activity}
                value={activity}
                onChange={(e) => setActivity(e.target.value as Activity)}
                disabled={busy}
              >
                {ACTIVITY.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </NativeSelect>
            </div>
          )}
          <div className="space-y-1">
            <Label htmlFor={ids.persona}>人设</Label>
            <NativeSelect id={ids.persona} value={personaId} onChange={(e) => setPersonaId(e.target.value)} disabled={busy}>
              <option value="">不用人设（以后可以再选）</option>
              {(personas ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </NativeSelect>
          </div>
        </div>
      )}
      {groups && !groups.fresh && can(bot, 'list_channels') && (
        <p className="text-xs text-muted-foreground">现在连不上，下面是这个号上次连上时所在的群。</p>
      )}
      {error && (
        <p id={ids.error} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button size="sm" disabled={busy || !chosen} onClick={() => void add()} aria-describedby={error ? ids.error : undefined}>
          {busy ? '启用中…' : '启用'}
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={close}>
          取消
        </Button>
      </div>
    </div>
  )
}
