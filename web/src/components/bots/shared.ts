import { useRef, useState } from 'react'

import type { Activity, Bot, BotChannel, BotMode, Outcome, Persona } from '@/lib/api'
import { charCount } from '@/lib/forms'

export const MODES: { mode: BotMode; label: string; hint: string }[] = [
  { mode: 'observe', label: '只看不说', hint: '判断要不要回、会说什么，但不会真的发出去' },
  { mode: 'paused', label: '暂停', hint: '不判断、不调用模型' },
  { mode: 'live', label: '正式发言', hint: '判断后真的发到群里' },
]

export const ACTIVITY: { value: Activity; label: string }[] = [
  { value: 'mentions', label: '只回点名' },
  { value: 'occasional', label: '偶尔插话' },
  { value: 'active', label: '活跃' },
]

export const OUTCOMES: { key: Outcome; label: string }[] = [
  { key: 'replied', label: '已发送' },
  { key: 'would_reply', label: '本来会回' },
  { key: 'skipped', label: '想了没回' },
  { key: 'suppressed', label: '被拦下' },
  { key: 'failed', label: '出错' },
]

export const isPrivate = (channel: BotChannel) => channel.kind === 'private'

export const groupName = (channel: BotChannel) =>
  isPrivate(channel) ? `${channel.name || channel.group_id} 的私聊` : channel.name || `群 ${channel.group_id}`

export const channelPath = (botId: string, channelId: number) =>
  `/api/bots/${encodeURIComponent(botId)}/channels/${channelId}`

/** The brain's prompt when a group has no persona. */
export const defaultPersona = (botName: string) => `你是群里的${botName}。`

/** One action at a time with its own busy flag and error, then a reload. Resolves true when it succeeded. */
export function useAction(reload: () => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // A ref, not `busy`: Enter and blur can both fire before the re-render that disables the control.
  const inFlight = useRef(false)
  const run = async (action: () => Promise<unknown>): Promise<boolean> => {
    if (inFlight.current) return false
    inFlight.current = true
    setBusy(true)
    setError('')
    try {
      await action()
      reload()
      return true
    } catch (e) {
      setError((e as Error).message)
      return false
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  return { busy, error, setError, run }
}

/**
 * A value being saved or saved a moment ago, shown in place of the server's until the refetch brings it (or any
 * other change) back, so a control does not jump to its old value meanwhile. Not a draft: `mark` it when the
 * request starts and `drop` it if the request fails.
 */
export function useJustSaved<T>(server: T) {
  const [saved, setSaved] = useState<{ value: T; over: T } | null>(null)
  // The server value moved on from the one this was saved over: the refetch arrived, show the server again.
  if (saved && !Object.is(saved.over, server)) setSaved(null)
  return {
    value: saved && Object.is(saved.over, server) ? saved.value : server,
    mark: (value: T) => setSaved({ value, over: server }),
    drop: () => setSaved(null),
  }
}

// ---- The bot form (显示名 · 别名 · 模型) ----

/** What the bot form edits, as typed. `providerId` '' = no model. */
export type BotFormState = { name: string; aliases: string; providerId: string; backupId: string }

export const botForm = (bot: Bot): BotFormState => ({
  name: bot.name,
  aliases: bot.aliases.join('、'),
  providerId: bot.provider_id ?? '',
  backupId: bot.backup_provider_id ?? '',
})

// ---- Names and aliases (the backend's limits: 1–20 code points, no control characters) ----

export const NAME_MAX = 20
export const ALIASES_MAX = 10
export const ALIAS_LIMITS = `别名最多 ${ALIASES_MAX} 个，每个最多 ${NAME_MAX} 字`
export const ONE_CHAR = '只有一个字的名字很容易被误认为在叫它'

/** What the backend refuses in names: control characters (Cc: newlines, tabs…), lone surrogates (Cs) and the
 * line/paragraph separators (Zl, Zp). */
const hasControl = (text: string) =>
  [...text].some((c) => {
    const code = c.codePointAt(0) ?? 0
    return (
      code < 0x20 ||
      (code >= 0x7f && code <= 0x9f) ||
      (code >= 0xd800 && code <= 0xdfff) ||
      code === 0x2028 ||
      code === 0x2029
    )
  })

/** Split what was typed into the aliases that would be saved: trimmed, no empties, first of each duplicate. */
export function parseAliases(text: string): string[] {
  const aliases: string[] = []
  for (const part of text.split(/[、,，;；\n]+/)) {
    const alias = part.trim()
    if (alias && !aliases.includes(alias)) aliases.push(alias)
  }
  return aliases
}

/** Why an alias cannot be saved, or null. `index` is its position in the parsed list. */
export function aliasProblem(alias: string, index: number): string | null {
  if (index >= ALIASES_MAX || charCount(alias) > NAME_MAX) return ALIAS_LIMITS
  if (hasControl(alias)) return '别名里有看不见的特殊字符，请删掉'
  return null
}

export function nameProblem(name: string): string | null {
  const trimmed = name.trim()
  if (!trimmed) return '显示名不能为空'
  if (charCount(trimmed) > NAME_MAX) return `显示名最多 ${NAME_MAX} 字`
  if (hasControl(trimmed)) return '显示名里有换行或看不见的特殊字符，请删掉'
  return null
}

export const sameList = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((item, i) => item === b[i])

// ---- Personas (the backend's limits: name 1–40, content 1–4000 code points) ----

export const PERSONA_CONTENT_MAX = 4000
export const PERSONA_NAME_MAX = 40

/** The persona editor's draft: which persona it was started on, and the text. */
export type PersonaDraft = { id: number | null; content: string }

export function personaNameProblem(name: string, personas: Persona[] | null, self: number | null): string | null {
  if (!name) return '人设名字不能为空'
  if (charCount(name) > PERSONA_NAME_MAX) return `人设名字最多 ${PERSONA_NAME_MAX} 字`
  if (personas?.some((p) => p.name === name && p.id !== self)) return `已经有叫 ${name} 的人设`
  return null
}

// ---- Folding (what is open on the Bot page, remembered in this browser) ----

const readOpen = (key: string, fallback: boolean): boolean => {
  try {
    const stored = window.localStorage.getItem(key)
    return stored === null ? fallback : stored === '1'
  } catch {
    return fallback // storage blocked (private window, previews): just the default
  }
}

/** An open/closed state remembered per browser under `key`; `fallback` until it was toggled once. */
export function useRememberedOpen(key: string, fallback: boolean) {
  const [open, setOpen] = useState(() => readOpen(key, fallback))
  const toggle = () =>
    setOpen((was) => {
      try {
        window.localStorage.setItem(key, was ? '0' : '1')
      } catch {
        // not remembered; the page still works
      }
      return !was
    })
  return { open, toggle }
}
