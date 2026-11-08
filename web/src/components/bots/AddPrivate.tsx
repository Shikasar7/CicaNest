import { useId, useState } from 'react'

import { useAction } from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { sendJson, type Bot, type Persona, type PrivateCreate } from '@/lib/api'
import { usePlatforms } from '@/lib/live'
import { platformOf, userLabel, userProblem } from '@/lib/platforms'

/** 回私聊: the bot answers this person's private messages (and only the people added here). */
export function AddPrivate({ bot, personas, reload }: { bot: Bot; personas: Persona[] | null; reload: () => void }) {
  const [open, setOpen] = useState(false)
  const [userId, setUserId] = useState('')
  const [personaId, setPersonaId] = useState('')
  const { busy, error, setError, run } = useAction(reload)
  const ids = { user: useId(), persona: useId(), error: useId() }
  const { data: platforms } = usePlatforms()
  const numeric = (platformOf(bot, platforms)?.key ?? 'qq') !== 'wecom'

  const start = () => {
    setOpen(true)
    setUserId('')
    setPersonaId('')
    setError('')
  }
  const close = () => setOpen(false)

  const add = async () => {
    const user = userId.trim()
    const problem = userProblem(user, bot, platforms)
    if (problem) {
      setError(problem)
      return
    }
    if (bot.mode === 'live' && !window.confirm(`添加后 ${bot.name} 会回 ${user} 发来的每条私聊。继续？`)) return
    const body: PrivateCreate = { user_id: user }
    if (personaId) body.persona_id = Number(personaId)
    if (await run(() => sendJson('POST', `/api/bots/${encodeURIComponent(bot.id)}/private`, body))) close()
  }

  if (!open) {
    return (
      <Button size="sm" variant="outline" onClick={start}>
        添加私聊对象
      </Button>
    )
  }
  return (
    <div
      role="group"
      aria-label="添加私聊对象"
      className="space-y-3 rounded-md border p-3"
      onKeyDown={(e) => e.key === 'Escape' && close()}
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor={ids.user}>对方的 {userLabel(bot, platforms)}</Label>
          <Input
            id={ids.user}
            className="w-48"
            inputMode={numeric ? 'numeric' : 'text'}
            autoComplete="off"
            value={userId}
            readOnly={busy}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? ids.error : undefined}
            onChange={(e) => {
              setError('')
              setUserId(e.target.value)
            }}
            onKeyDown={(e) => e.key === 'Enter' && void add()}
          />
        </div>
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
      <p className="text-xs text-muted-foreground">只回这里加过的人；别人私聊这个号，bot 不会回。</p>
      {error && (
        <p id={ids.error} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} onClick={() => void add()}>
          {busy ? '添加中…' : '添加'}
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={close}>
          取消
        </Button>
      </div>
    </div>
  )
}
