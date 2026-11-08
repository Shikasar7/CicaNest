import { FileDiff, Rss, Tag, type LucideIcon } from 'lucide-react'

import type { Tone } from '@/components/StatusDot'
import type {
  WatchCreate,
  WatchKind,
  WatchMode,
  WatchPatch,
  WatchRun,
  WatchTargetKind,
  WatchView,
} from '@/lib/api'
import { charCount, parseNumber } from '@/lib/forms'
import { formatAgo, formatTime } from '@/lib/format'

export const KINDS: { kind: WatchKind; label: string; Icon: LucideIcon; hint: string; urlHint: string }[] = [
  {
    kind: 'feed',
    label: '订阅',
    Icon: Rss,
    hint: '网站的订阅（RSS 或 Atom）出现新文章时推送',
    urlHint: '订阅地址，通常以 /feed、/rss 或 .xml 结尾',
  },
  {
    kind: 'page',
    label: '网页变化',
    Icon: FileDiff,
    hint: '网页上你选的那一块文字变了时推送',
    urlHint: '要查看的网页地址',
  },
  {
    kind: 'price',
    label: '价格',
    Icon: Tag,
    hint: '网页上的价格变了时推送',
    urlHint: '商品页面的地址',
  },
]

export const kindOf = (kind: WatchKind) => KINDS.find((k) => k.kind === kind) ?? KINDS[0]

export const MODES: { value: WatchMode; label: string; hint: string }[] = [
  { value: 'preview', label: '试运行', hint: '照常检查，本来要发的消息只显示在这里，不会真的发出去' },
  { value: 'push', label: '推送', hint: '有新内容时真的发出去' },
]

export const INTERVAL_MIN = 5
export const INTERVAL_MAX = 1440
export const CUSTOM = 'custom'
export const INTERVALS: { minutes: number; label: string }[] = [
  { minutes: 10, label: '10 分钟' },
  { minutes: 30, label: '30 分钟' },
  { minutes: 60, label: '1 小时' },
  { minutes: 180, label: '3 小时' },
  { minutes: 360, label: '6 小时' },
  { minutes: 1440, label: '每天' },
]

export function intervalText(minutes: number): string {
  if (minutes === 1440) return '每天'
  if (minutes === 60) return '每小时'
  if (minutes % 60 === 0) return `每 ${minutes / 60} 小时`
  return `每 ${minutes} 分钟`
}

const pad = (n: number) => String(n).padStart(2, '0')

/** "在 14:20" today, "在明天 08:00", else "在 10-28 14:20". */
function at(seconds: number): string {
  const date = new Date(seconds * 1000)
  const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  const today = new Date()
  if (date.toDateString() === today.toDateString()) return `在 ${hm}`
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1)
  if (date.toDateString() === tomorrow.toDateString()) return `在明天 ${hm}`
  return `在 ${formatTime(seconds)}`
}

/** When the scheduler looks next. `now` in unix seconds. */
export function nextText(watch: WatchView, now: number): string {
  if (!watch.enabled) return '已停用'
  if (watch.next_run_at === null || watch.next_run_at - now < 30) return '马上会查'
  return `下次${at(watch.next_run_at)}`
}

// ---- Runs ----

export function runSummary(run: WatchRun, kind: WatchKind): string {
  switch (run.outcome) {
    case 'baseline':
      return '第一次检查，记下了现在的内容'
    case 'unchanged':
      return '没有变化'
    case 'changed':
      if (kind === 'feed') return `有 ${run.changes} 条新内容`
      return kind === 'price' ? '价格变了' : '有变化'
    case 'error':
      return `出错：${run.error || run.detail || '原因不明'}`
  }
}

/** The card's 上次 line: "3 分钟前：没有变化", "刚刚出错：连不上". */
export function lastCheckText(run: WatchRun, kind: WatchKind): string {
  const ago = formatAgo(run.started_at)
  if (run.outcome !== 'error') return `${ago}：${runSummary(run, kind)}`
  return `${ago}${/\d$/.test(ago) ? ' ' : ''}出错：${run.error || run.detail || '原因不明'}`
}

/** Quiet grey when nothing happened, green when something new showed up, red when the check failed. */
export function runTone(run: WatchRun): Tone {
  if (run.outcome === 'error') return 'bad'
  return run.outcome === 'changed' ? 'ok' : 'off'
}

/** Under a message: where it went, or why it did not. */
export function deliveryText(run: WatchRun, target: string, botName: string): { text: string; bad: boolean } {
  const ago = formatAgo(run.started_at)
  switch (run.delivered) {
    case 'sent':
      return { text: `发到「${target}」，${ago}`, bad: false }
    case 'preview':
      return { text: `本来会发到「${target}」，${ago}`, bad: false }
    case 'paused':
      return { text: `${botName} 已暂停，没有发到「${target}」，${ago}`, bad: false }
    case 'failed':
      return { text: `发到「${target}」失败，${ago}`, bad: true }
    case 'unknown':
      return { text: `发往「${target}」，没收到平台的确认，不确定发出去没有，${ago}`, bad: false }
    default:
      return { text: `没有发到「${target}」，${ago}`, bad: false }
  }
}

// ---- The form ----

export const PRIVATE = 'private'
export const groupTarget = (groupId: string) => `group:${groupId}`

/** Everything the form edits, as typed. `botId` / `target` '' = the first one offered. */
export type WatchFormState = {
  name: string
  kind: WatchKind
  url: string
  selector: string
  threshold: string
  /** A preset's minutes, or CUSTOM. */
  interval: string
  customMinutes: string
  botId: string
  /** groupTarget(id), PRIVATE, or '' (the bot's first group). */
  target: string
  privateId: string
  /** '' = 不用，直接列出. */
  providerId: string
  instructions: string
  useProxy: boolean
  mode: WatchMode
}

export const EMPTY_FORM: WatchFormState = {
  name: '',
  kind: 'feed',
  url: '',
  selector: '',
  threshold: '',
  interval: '60',
  customMinutes: '',
  botId: '',
  target: '',
  privateId: '',
  providerId: '',
  instructions: '',
  useProxy: false,
  mode: 'preview',
}

export function watchForm(watch: WatchView): WatchFormState {
  const preset = INTERVALS.some((i) => i.minutes === watch.interval_minutes)
  return {
    name: watch.name,
    kind: watch.kind,
    url: watch.url,
    selector: watch.selector,
    threshold: watch.threshold === null ? '' : String(watch.threshold),
    interval: preset ? String(watch.interval_minutes) : CUSTOM,
    customMinutes: preset ? '' : String(watch.interval_minutes),
    botId: watch.bot_id,
    target: watch.target_kind === 'group' ? groupTarget(watch.target_id) : PRIVATE,
    privateId: watch.target_kind === 'private' ? watch.target_id : '',
    providerId: watch.provider_id ?? '',
    instructions: watch.instructions,
    useProxy: Boolean(watch.use_proxy),
    mode: watch.mode,
  }
}

export type WatchFormField =
  | 'name'
  | 'url'
  | 'selector'
  | 'threshold'
  | 'interval'
  | 'bot'
  | 'target'
  | 'privateId'
  | 'provider'
  | 'instructions'
  | 'proxy'
export type WatchFormErrors = Partial<Record<WatchFormField, string>>

/** What would be saved, checked. */
export type WatchValues = {
  name: string
  kind: WatchKind
  url: string
  selector: string
  threshold: number | null
  interval_minutes: number
  bot_id: string
  target_kind: WatchTargetKind
  target_id: string
  provider_id: string | null
  instructions: string
  use_proxy: boolean
  mode: WatchMode
}

export type CheckedForm = { ok: true; values: WatchValues } | { ok: false; errors: WatchFormErrors }

function isHttpUrl(text: string): boolean {
  try {
    const url = new URL(text)
    return (url.protocol === 'http:' || url.protocol === 'https:') && !!url.hostname
  } catch {
    return false
  }
}

/** Validate what the backend would, so mistakes show next to the field before anything is sent. */
export function checkForm(form: WatchFormState, resolved: { botId: string; target: string }): CheckedForm {
  const errors: WatchFormErrors = {}
  const name = form.name.trim()
  if (!name) errors.name = '填一个名字，例如 少数派新文章'
  else if (charCount(name) > 40) errors.name = '名字最多 40 字'

  const url = form.url.trim()
  if (!url) errors.url = '填要检查的地址'
  else if (charCount(url) > 1000) errors.url = '地址最多 1000 个字符'
  else if (!isHttpUrl(url)) errors.url = '地址要以 http:// 或 https:// 开头'

  const selector = form.kind === 'feed' ? '' : form.selector.trim()
  if (form.kind === 'price' && !selector) errors.selector = '价格要填选择器，告诉 nest 价格在页面上的哪个位置'
  else if (charCount(selector) > 300) errors.selector = '选择器最多 300 个字符'

  let threshold: number | null = null
  if (form.kind === 'price') {
    const parsed = parseNumber(form.threshold.replace(/[,，¥￥\s]/g, ''))
    if (!parsed.ok || (parsed.value !== null && parsed.value <= 0)) errors.threshold = '填一个大于 0 的数，例如 199'
    else threshold = parsed.value
  }

  const minutes = parseNumber(form.interval === CUSTOM ? form.customMinutes : form.interval, {
    min: INTERVAL_MIN,
    max: INTERVAL_MAX,
    integer: true,
  })
  if (!minutes.ok || minutes.value === null) errors.interval = `填 ${INTERVAL_MIN}–${INTERVAL_MAX} 之间的整数分钟`

  if (!resolved.botId) errors.bot = '选一个 bot'
  let targetKind: WatchTargetKind = 'group'
  let targetId = ''
  if (resolved.target === PRIVATE) {
    targetKind = 'private'
    targetId = form.privateId.trim()
    // Other platforms' ids are for the server to judge: it knows their forms.
    if (resolved.botId.startsWith('qq:') && !/^\d{5,12}$/.test(targetId)) errors.privateId = 'QQ 号是 5–12 位数字'
    if (!targetId) errors.privateId = '填对方的 ID'
  } else if (resolved.target.startsWith('group:')) {
    targetId = resolved.target.slice('group:'.length)
  } else {
    errors.target = '选一个群，或者选私聊'
  }

  const providerId = form.providerId || null
  const instructions = form.instructions.trim()
  if (providerId && charCount(instructions) > 500) errors.instructions = '额外要求最多 500 字'

  if (Object.keys(errors).length || !minutes.ok || minutes.value === null) return { ok: false, errors }
  return {
    ok: true,
    values: {
      name,
      kind: form.kind,
      url,
      selector,
      threshold,
      interval_minutes: minutes.value,
      bot_id: resolved.botId,
      target_kind: targetKind,
      target_id: targetId,
      provider_id: providerId,
      instructions,
      use_proxy: form.useProxy,
      mode: form.mode,
    },
  }
}

export function createBody(v: WatchValues): WatchCreate {
  return {
    name: v.name,
    kind: v.kind,
    url: v.url,
    selector: v.selector,
    ...(v.kind === 'price' && v.threshold !== null ? { threshold: v.threshold } : {}),
    interval_minutes: v.interval_minutes,
    bot_id: v.bot_id,
    target_kind: v.target_kind,
    target_id: v.target_id,
    provider_id: v.provider_id,
    instructions: v.provider_id ? v.instructions : '',
    use_proxy: v.use_proxy,
    mode: v.mode,
    enabled: true,
  }
}

/** Only what changed. The target goes as a whole (bot, kind, id); 额外要求 only while a model is chosen. */
export function patchBody(v: WatchValues, w: WatchView): WatchPatch {
  const body: WatchPatch = {}
  if (v.name !== w.name) body.name = v.name
  if (v.kind !== w.kind) body.kind = v.kind
  if (v.url !== w.url) body.url = v.url
  if (v.selector !== w.selector) body.selector = v.selector
  if (v.threshold !== w.threshold) body.threshold = v.threshold
  if (v.interval_minutes !== w.interval_minutes) body.interval_minutes = v.interval_minutes
  if (v.bot_id !== w.bot_id || v.target_kind !== w.target_kind || v.target_id !== w.target_id) {
    body.bot_id = v.bot_id
    body.target_kind = v.target_kind
    body.target_id = v.target_id
  }
  if (v.provider_id !== w.provider_id) body.provider_id = v.provider_id
  if (v.provider_id && v.instructions !== w.instructions) body.instructions = v.instructions
  if (v.use_proxy !== Boolean(w.use_proxy)) body.use_proxy = v.use_proxy
  if (v.mode !== w.mode) body.mode = v.mode
  return body
}

/** Changing what is watched makes the next check a fresh start (it remembers, pushes nothing). */
export const startsOver = (v: WatchValues, w: WatchView) => v.kind !== w.kind || v.url !== w.url || v.selector !== w.selector

/** Where to show one of the backend's Chinese 4xx sentences: next to the field it names, if it names one. */
const ERROR_FIELDS: [RegExp, WatchFormField][] = [
  [/额外要求/, 'instructions'],
  [/选择器/, 'selector'],
  [/低于|门槛|价格/, 'threshold'],
  [/地址|网址/, 'url'],
  [/QQ ?号|用户 ID|userid|私聊/, 'privateId'],
  [/群/, 'target'],
  [/模型/, 'provider'],
  [/分钟|多久|间隔/, 'interval'],
  [/代理/, 'proxy'],
  [/名字|已经有叫/, 'name'],
  [/bot/i, 'bot'],
]

export function errorField(text: string): WatchFormField | undefined {
  return ERROR_FIELDS.find(([pattern]) => pattern.test(text))?.[1]
}

export const targetLabel = (group: { group_id: string; name: string }) =>
  group.name ? `${group.name}（${group.group_id}）` : `群 ${group.group_id}`
