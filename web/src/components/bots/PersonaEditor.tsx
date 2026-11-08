import { useEffect, useId, useRef, useState } from 'react'

import {
  PERSONA_CONTENT_MAX,
  channelPath,
  defaultPersona,
  groupName,
  personaNameProblem,
  type PersonaDraft,
  type useAction,
} from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { sendJson, type Bot, type BotChannel, type Ok, type Persona } from '@/lib/api'
import { CHANGED_ELSEWHERE, type Draft } from '@/lib/drafts'
import { charCount, describedBy, editKeys } from '@/lib/forms'
import { cn } from '@/lib/utils'

/**
 * The persona a group uses, edited in place: its text (shared with every group that uses it), 改名 and 删除.
 * Mounted while open; the draft and the busy/error state belong to the owner, so closing loses nothing.
 */
export function PersonaEditor({
  bot,
  channel,
  personaId,
  persona,
  personaName,
  personas,
  draft,
  editor,
  settling,
  onClose,
  onReleased,
}: {
  bot: Bot
  channel: BotChannel
  /** The persona being edited: the group's persona_id when the editor opened. */
  personaId: number
  /** The /api/personas entry, null until the list has loaded. */
  persona: Persona | null
  personaName: string
  personas: Persona[] | null
  draft: Draft<PersonaDraft>
  editor: ReturnType<typeof useAction>
  /** A new choice in the select is on its way: the text here is about to change, so it is read-only. */
  settling: boolean
  /** Close and give focus back to the toggle. */
  onClose: () => void
  /** This group no longer uses the persona (deleted, or released before a failed delete). */
  onReleased: () => void
}) {
  const ids = useId()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const renameRef = useRef<HTMLInputElement>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameSaving, setRenameSaving] = useState(false)
  const [renameError, setRenameError] = useState('')
  const isRenaming = renaming !== null
  const group = groupName(channel)
  const id = personaId

  useEffect(() => {
    textareaRef.current?.focus() // mounted exactly when 编辑 opens it
  }, [])
  useEffect(() => {
    if (isRenaming) renameRef.current?.focus()
  }, [isRenaming])

  const content = draft.value.content
  const count = charCount(content)
  const over = count > PERSONA_CONTENT_MAX
  const otherUses = (persona?.used_by ?? []).filter(
    (u) => !(u.bot_id === bot.id && u.channel_id === channel.channel_id),
  )
  const usesText = otherUses.map((u) => `${u.bot_name}（${u.channel_name}）`).join('、')
  const busy = editor.busy || renameSaving

  const cancel = () => {
    if (busy) return
    draft.clear()
    editor.setError('')
    onClose()
  }
  const save = async () => {
    if (!draft.dirty) {
      draft.clear()
      onClose()
      return
    }
    // The draft remembers which persona it was started on, even if the group switched meanwhile.
    const target = draft.value.id
    if (over || target === null) return
    if (!content.trim()) {
      editor.setError('人设内容不能为空；不想用人设就在上面选「不用人设」')
      return
    }
    if (await editor.run(() => sendJson<Persona>('PATCH', `/api/personas/${target}`, { content }))) {
      draft.clear()
      onClose()
    }
  }

  const rename = async () => {
    if (renaming === null || renameSaving) return
    const name = renaming.trim()
    if (name === personaName) {
      setRenaming(null)
      return
    }
    const problem = personaNameProblem(name, personas, id)
    if (problem) {
      setRenameError(problem)
      return
    }
    setRenameSaving(true)
    setRenameError('')
    try {
      await sendJson<Persona>('PATCH', `/api/personas/${id}`, { name })
      setRenaming(null)
      editor.setError('')
    } catch (e) {
      setRenameError((e as Error).message) // a 409 (name taken) lands here, under the field
    } finally {
      setRenameSaving(false)
    }
  }

  // Deleting releases this group first: the backend refuses a persona that is still used anywhere.
  const remove = async () => {
    if (persona === null || otherUses.length > 0) return // without the list, other uses are unknown
    const lose = draft.dirty ? '还没保存的修改也会丢掉。' : ''
    const message = `删除人设「${personaName}」？${group} 会改成不用人设，用默认：${defaultPersona(bot.name)}${lose}`
    if (!window.confirm(message)) return
    const step = { released: false }
    await editor.run(async () => {
      await sendJson<Ok>('PATCH', channelPath(bot.id, channel.channel_id), { persona_id: null })
      step.released = true
      try {
        await sendJson<Ok>('DELETE', `/api/personas/${id}`)
      } catch (e) {
        throw new Error(`这个群已改成不用人设，但人设没能删除：${(e as Error).message}`)
      }
    })
    if (step.released) onReleased()
  }

  const contentId = `${ids}-content`
  const counterId = `${ids}-counter`
  const renameId = `${ids}-rename`
  const renameErrorId = `${ids}-rename-error`
  return (
    <div
      role="group"
      aria-label={`编辑人设 ${personaName}`}
      className="space-y-2 rounded-md border p-3"
      onKeyDown={(e) => editKeys({ onEscape: cancel })(e)}
    >
      {draft.changedElsewhere && (
        <p role="status" className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
          {CHANGED_ELSEWHERE}
        </p>
      )}
      {otherUses.length > 0 && (
        <p className="text-xs text-amber-700 dark:text-amber-400">这个人设也用在：{usesText}，保存后一起生效</p>
      )}
      {persona === null && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          还没读到这个人设用在哪些群；如果也用在别处，保存后一起生效
        </p>
      )}
      <Label htmlFor={contentId}>人设内容</Label>
      <Textarea
        id={contentId}
        ref={textareaRef}
        className="max-h-96 min-h-28 overflow-y-auto"
        value={content}
        readOnly={busy || settling}
        aria-invalid={over ? true : undefined}
        aria-describedby={counterId}
        onChange={(e) => {
          editor.setError('')
          draft.set((current) => ({ ...current, content: e.target.value }))
        }}
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span id={counterId} className={cn(over ? 'text-destructive' : 'text-muted-foreground')}>
          {count}/{PERSONA_CONTENT_MAX}
        </span>
        {draft.dirty && <span className="text-amber-700 dark:text-amber-400">有还没保存的修改</span>}
      </div>
      {over && (
        <p role="alert" className="text-xs text-destructive">
          超过 {PERSONA_CONTENT_MAX} 字，删短一些才能保存
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy || over} onClick={() => void save()}>
          {editor.busy ? '保存中…' : '保存'}
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={cancel}>
          取消
        </Button>
        <span className="text-xs text-muted-foreground">改的是人设本身，保存后立刻生效</span>
      </div>
      {editor.error && (
        <p role="alert" className="text-xs text-destructive">
          {editor.error}
        </p>
      )}
      <div className="space-y-1 border-t pt-2">
        {isRenaming ? (
          <div
            className="flex flex-wrap items-center gap-2"
            onKeyDown={(e) => {
              e.stopPropagation() // Esc here leaves 改名, not the whole editor
              editKeys({ onEnter: () => void rename(), onEscape: () => setRenaming(null) })(e)
            }}
          >
            <Label htmlFor={renameId} className="text-xs font-normal text-muted-foreground">
              新名字
            </Label>
            <Input
              id={renameId}
              ref={renameRef}
              className="w-56"
              value={renaming}
              autoComplete="off"
              readOnly={renameSaving}
              aria-invalid={renameError ? true : undefined}
              aria-describedby={describedBy(renameError && renameErrorId)}
              onChange={(e) => {
                setRenameError('')
                setRenaming(e.target.value)
              }}
            />
            <Button size="sm" disabled={renameSaving} onClick={() => void rename()}>
              {renameSaving ? '保存中…' : '保存名字'}
            </Button>
            <Button size="sm" variant="outline" disabled={renameSaving} onClick={() => setRenaming(null)}>
              取消
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => {
                setRenameError('')
                setRenaming(personaName)
              }}
            >
              改名
            </Button>
            <Button
              size="sm"
              variant="destructive"
              disabled={busy || persona === null || otherUses.length > 0}
              onClick={() => void remove()}
            >
              删除这个人设
            </Button>
          </div>
        )}
        {renameError && (
          <p id={renameErrorId} role="alert" className="text-xs text-destructive">
            {renameError}
          </p>
        )}
        {otherUses.length > 0 && <p className="text-xs text-muted-foreground">还用在：{usesText}，不能删除</p>}
        {persona === null && <p className="text-xs text-muted-foreground">人设列表还没读到，暂时不能删除</p>}
      </div>
    </div>
  )
}
