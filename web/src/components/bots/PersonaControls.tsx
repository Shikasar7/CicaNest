import { useEffect, useId, useRef, useState } from 'react'

import { PersonaEditor } from '@/components/bots/PersonaEditor'
import {
  PERSONA_CONTENT_MAX,
  PERSONA_NAME_MAX,
  channelPath,
  defaultPersona,
  groupName,
  personaNameProblem,
  useAction,
  useJustSaved,
  type PersonaDraft,
} from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { ApiError, sendJson, type Bot, type BotChannel, type Ok, type Persona } from '@/lib/api'
import { useDraft } from '@/lib/drafts'
import { charCount, describedBy, editKeys } from '@/lib/forms'

const NONE = 'none'
const NEW = 'new'

const clip = (text: string, max: number) => [...text].slice(0, max).join('')

/** `{bot}@{group}`, with a number added when that name is taken. */
function suggestName(base: string, personas: Persona[] | null): string {
  const taken = new Set((personas ?? []).map((p) => p.name))
  let name = clip(base, PERSONA_NAME_MAX)
  for (let n = 2; taken.has(name) && n < 100; n++) name = `${clip(base, PERSONA_NAME_MAX - String(n).length - 1)} ${n}`
  return name
}

/**
 * One group's persona: the select (+ 不用人设, + 新建人设…) and, behind a toggle, the editor for the persona
 * itself. The select and the editor each have their own busy and error state; the editor's draft lives here,
 * so collapsing the editor keeps it and the select can ask before dropping it.
 */
export function PersonaControls({
  bot,
  channel,
  personas,
  reload,
}: {
  bot: Bot
  channel: BotChannel
  /** null until the persona list has loaded: the row falls back to what /api/bots says. */
  personas: Persona[] | null
  reload: () => void
}) {
  const group = groupName(channel)
  const path = channelPath(bot.id, channel.channel_id)
  const persona = channel.persona_id === null ? null : (personas?.find((p) => p.id === channel.persona_id) ?? null)
  const personaName = persona?.name ?? channel.persona_name
  const baseContent = persona?.content ?? channel.persona
  const ids = useId()

  // ---- The select ----
  const select = useAction(reload)
  const serverChoice = channel.persona_id === null ? NONE : String(channel.persona_id)
  const choice = useJustSaved(serverChoice)
  // A choice was just saved and the refetch has not brought it yet: the editor below still shows the old one.
  const settling = choice.value !== serverChoice
  const selectRef = useRef<HTMLSelectElement>(null)

  // ---- 新建人设… (replaces the select while naming) ----
  const [newName, setNewName] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<{ name: boolean; text: string } | null>(null)
  const newNameRef = useRef<HTMLInputElement>(null)
  const naming = newName !== null
  const wasNaming = useRef(false)

  // ---- The editor ----
  const draft = useDraft<PersonaDraft>({ id: channel.persona_id, content: baseContent })
  const editor = useAction(reload)
  const [open, setOpen] = useState(false)
  const toggleRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (naming) newNameRef.current?.focus()
    else if (wasNaming.current) selectRef.current?.focus()
    wasNaming.current = naming
  }, [naming])

  const options: { id: number; name: string; unused: boolean }[] = (personas ?? []).map((p) => ({
    id: p.id,
    name: p.name,
    unused: p.used_by.length === 0,
  }))
  if (channel.persona_id !== null && !options.some((o) => o.id === channel.persona_id)) {
    options.unshift({ id: channel.persona_id, name: channel.persona_name || '（读取中）', unused: false })
  }

  // The text a new persona starts from.
  const start =
    draft.dirty && draft.value.content.trim()
      ? { content: draft.value.content, from: '内容用你刚才还没保存的修改' }
      : channel.persona_id !== null && baseContent.trim()
        ? { content: baseContent, from: `内容先复制「${personaName}」` }
        : { content: defaultPersona(bot.name), from: `内容先用默认的「${defaultPersona(bot.name)}」` }

  const choose = async (value: string) => {
    if (value === NEW) {
      setCreateError(null)
      setNewName(suggestName(`${bot.name}@${group}`, personas))
      return
    }
    if (value === choice.value) return
    if (draft.dirty && !window.confirm('放弃还没保存的修改？')) return
    draft.clear()
    editor.setError('')
    if (value === NONE) setOpen(false)
    choice.mark(value)
    const ok = await select.run(() => sendJson<Ok>('PATCH', path, { persona_id: value === NONE ? null : Number(value) }))
    if (!ok) choice.drop()
  }

  const cancelCreate = () => {
    if (!creating) setNewName(null)
  }

  const create = async () => {
    if (newName === null || creating) return
    const name = newName.trim()
    const problem = personaNameProblem(name, personas, null)
    if (problem) {
      setCreateError({ name: true, text: problem })
      newNameRef.current?.focus()
      return
    }
    if (charCount(start.content) > PERSONA_CONTENT_MAX) {
      setCreateError({ name: false, text: `人设内容超过 ${PERSONA_CONTENT_MAX} 字，删短一些才能保存` })
      return
    }
    setCreating(true)
    setCreateError(null)
    select.setError('')
    let created: Persona
    try {
      created = await sendJson<Persona>('POST', '/api/personas', { name, content: start.content })
    } catch (e) {
      setCreateError({ name: e instanceof ApiError && e.status === 409, text: (e as Error).message })
      setCreating(false)
      newNameRef.current?.focus()
      return
    }
    try {
      await sendJson<Ok>('PATCH', path, { persona_id: created.id })
      choice.mark(String(created.id))
      draft.clear() // a dirty draft lives on in the new persona
    } catch (e) {
      select.setError(`人设已建好，但没能用到这个群：${(e as Error).message}`)
    } finally {
      setNewName(null)
      setCreating(false)
      reload()
    }
  }

  const closeEditor = () => {
    setOpen(false)
    toggleRef.current?.focus()
  }

  const nameId = `${ids}-name`
  const nameErrorId = `${ids}-name-error`
  const selectId = `${ids}-select`
  return (
    <div className="space-y-2">
      {naming ? (
        <div className="space-y-1">
          <div
            role="group"
            aria-label={`给 ${group} 新建人设`}
            className="flex flex-wrap items-center gap-2"
            onKeyDown={(e) => editKeys({ onEnter: () => void create(), onEscape: cancelCreate })(e)}
          >
            <Label htmlFor={nameId} className="text-xs font-normal text-muted-foreground">
              新人设
            </Label>
            <Input
              id={nameId}
              ref={newNameRef}
              className="w-56 max-w-full"
              value={newName}
              placeholder="人设名字"
              autoComplete="off"
              readOnly={creating}
              aria-invalid={createError?.name ? true : undefined}
              aria-describedby={describedBy(createError && nameErrorId)}
              onChange={(e) => {
                setCreateError(null)
                setNewName(e.target.value)
              }}
            />
            <Button size="sm" disabled={creating} onClick={() => void create()}>
              {creating ? '创建中…' : '创建'}
            </Button>
            <Button size="sm" variant="outline" disabled={creating} onClick={cancelCreate}>
              取消
            </Button>
          </div>
          {createError && (
            <p id={nameErrorId} role="alert" className="text-xs text-destructive">
              {createError.text}
            </p>
          )}
          <p className="text-xs text-muted-foreground">{start.from}，建好后这个群就用它，之后还可以再改。</p>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor={selectId} className="text-xs font-normal text-muted-foreground">
            人设
          </Label>
          <NativeSelect
            id={selectId}
            ref={selectRef}
            className="max-w-full"
            value={choice.value}
            disabled={select.busy}
            onChange={(e) => void choose(e.target.value)}
          >
            <option value={NONE}>不用人设</option>
            {options.map((o) => (
              <option key={o.id} value={String(o.id)}>
                {o.unused ? `${o.name}（没在用）` : o.name}
              </option>
            ))}
            <option value={NEW}>新建人设…</option>
          </NativeSelect>
          {channel.persona_id !== null && (
            <button
              ref={toggleRef}
              type="button"
              className="text-left text-xs text-muted-foreground underline-offset-2 hover:underline disabled:opacity-50"
              aria-expanded={open}
              disabled={settling}
              onClick={() => (open ? closeEditor() : setOpen(true))}
            >
              人设：{personaName} · {open ? '收起' : '编辑'}
              {!open && draft.dirty && <span className="text-amber-700 dark:text-amber-400">（有还没保存的修改）</span>}
            </button>
          )}
        </div>
      )}
      {select.busy && (
        <p role="status" className="text-xs text-muted-foreground">
          正在保存…
        </p>
      )}
      {select.error && (
        <p role="alert" className="text-xs text-destructive">
          {select.error}
        </p>
      )}
      {channel.persona_id === null && !settling && (
        <p className="text-xs text-muted-foreground">没有人设，用默认：{defaultPersona(bot.name)}</p>
      )}
      {open && channel.persona_id !== null && (
        <PersonaEditor
          bot={bot}
          channel={channel}
          personaId={channel.persona_id}
          persona={persona}
          personaName={personaName}
          personas={personas}
          draft={draft}
          editor={editor}
          settling={settling}
          onClose={closeEditor}
          onReleased={() => {
            choice.mark(NONE)
            draft.clear()
            setOpen(false)
            reload() // also when only the first step worked
          }}
        />
      )}
      {editor.error && !open && (
        <p role="alert" className="text-xs text-destructive">
          {editor.error}
        </p>
      )}
    </div>
  )
}
