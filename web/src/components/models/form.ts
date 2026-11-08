import type { ProviderCreate, ProviderKind, ProviderOptions, ProviderPatch, ProviderView } from '@/lib/api'
import { charCount, numberText, parseNumber } from '@/lib/forms'
import { CODEX_BASE_URL, KIND_OPTIONS, RESERVED_EXTRA_KEYS, effortOf, urlHost } from '@/lib/models'

/** Everything the model form edits, as typed. The key lives only here, only while editing. */
export type ModelFormState = {
  id: string
  kind: ProviderKind
  base_url: string
  model: string
  proxy: string
  timeout: string
  temperature: string
  extra_body: string
  input_format: 'text' | 'messages'
  auth_header: 'x-api-key' | 'bearer'
  effort: 'low' | 'medium' | 'high'
  auth_file: string
  key: string
  /** The user typed in the key field during this edit. */
  keyTouched: boolean
  clearKey: boolean
}

export type ModelFormField = 'id' | 'base_url' | 'model' | 'proxy' | 'timeout' | 'temperature' | 'extra_body' | 'key'
export type ModelFormErrors = Partial<Record<ModelFormField, string>>

const text = (value: unknown) => (typeof value === 'string' ? value : '')

/** The form as it would look for `provider` (or an empty one for 添加模型). */
export function formFrom(provider: ProviderView | null): ModelFormState {
  const options = provider?.options ?? {}
  const extra = options.extra_body
  return {
    id: provider?.id ?? '',
    kind: provider?.kind ?? 'openai_chat',
    base_url: provider?.base_url ?? '',
    model: provider?.model ?? '',
    proxy: text(options.proxy),
    timeout: numberText(options.timeout),
    temperature: numberText(options.temperature),
    extra_body: extra === undefined || extra === null ? '' : JSON.stringify(extra, null, 2),
    input_format: options.input_format === 'messages' ? 'messages' : 'text',
    auth_header: options.auth_header === 'bearer' ? 'bearer' : 'x-api-key',
    effort: effortOf(options.effort),
    auth_file: text(options.auth_file),
    key: '',
    keyTouched: false,
    clearKey: false,
  }
}

/** Draft equality that ignores "touched" bookkeeping: typing a key and deleting it again is no change. */
export function sameForm(a: ModelFormState, b: ModelFormState): boolean {
  return JSON.stringify({ ...a, keyTouched: false }) === JSON.stringify({ ...b, keyTouched: false })
}

/** Switch kind, dropping the values the new kind does not use (switching back does not bring them back). */
export function withKind(form: ModelFormState, kind: ProviderKind): ModelFormState {
  if (kind === form.kind) return form
  const allowed = KIND_OPTIONS[kind]
  const next: ModelFormState = { ...form, kind }
  if (!allowed.includes('temperature')) next.temperature = ''
  if (!allowed.includes('extra_body')) next.extra_body = ''
  if (!allowed.includes('input_format')) next.input_format = 'text'
  if (!allowed.includes('auth_header')) next.auth_header = 'x-api-key'
  if (!allowed.includes('effort')) next.effort = 'low'
  if (!allowed.includes('auth_file')) next.auth_file = ''
  if (kind === 'codex') {
    // Codex only talks to chatgpt.com and uses the login file instead of a key.
    if (urlHost(form.base_url) !== 'chatgpt.com') next.base_url = CODEX_BASE_URL
    next.key = ''
    next.keyTouched = false
    next.clearKey = false
  } else if (form.kind === 'codex' && form.base_url.trim() === CODEX_BASE_URL) {
    next.base_url = ''
  }
  return next
}

const ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,39}$/
const KEY_PATTERN = /^[\x21-\x7e]{1,500}$/
const PROXY_PATTERN = /^https?:\/\/[^\s/?#@]+:\d{1,5}$/i
const NO_EXTRAS = '地址里不要带用户名、密码或 ? 参数，密钥请填在「密钥」里'

function checkBaseUrl(value: string, kind: ProviderKind): string | undefined {
  const url = value.trim()
  if (!url) return kind === 'codex' ? undefined : '填写地址'
  if (charCount(url) > 300) return '地址最多 300 个字符'
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return '地址要以 http:// 或 https:// 开头'
  }
  if ((parsed.protocol !== 'http:' && parsed.protocol !== 'https:') || !parsed.hostname) {
    return '地址要以 http:// 或 https:// 开头'
  }
  if (parsed.username || parsed.password || url.includes('?') || url.includes('#')) return NO_EXTRAS
  if (kind === 'codex' && (parsed.protocol !== 'https:' || parsed.hostname !== 'chatgpt.com')) {
    return `Codex 订阅的地址要用 ${CODEX_BASE_URL}`
  }
  return undefined
}

/** "" or http(s)://host:port; one trailing slash is forgiven. */
function normalizeProxy(value: string): string {
  return value.trim().replace(/\/$/, '')
}

/** The pattern alone lets typos like http://[::1:7890 through; the URL parser catches those. */
function isProxy(proxy: string): boolean {
  if (!PROXY_PATTERN.test(proxy)) return false
  try {
    new URL(proxy)
    return true
  } catch {
    return false
  }
}

export function checkProxy(value: string): string | undefined {
  const proxy = normalizeProxy(value)
  if (!proxy || isProxy(proxy)) return undefined
  if (/^socks/i.test(proxy)) return '暂时只支持 http 代理，例如 http://127.0.0.1:7890'
  return '代理填 http://地址:端口，例如 http://127.0.0.1:7890'
}

function parseExtra(value: string): { ok: true; value: Record<string, unknown> | null } | { ok: false; error: string } {
  const raw = value.trim()
  if (!raw) return { ok: true, value: null }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { ok: false, error: '额外参数不是有效的 JSON，检查引号和逗号' }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: '额外参数要是一个 JSON 对象，例如 {"top_p": 0.9}' }
  }
  const reserved = Object.keys(parsed).find((key) => RESERVED_EXTRA_KEYS.includes(key))
  if (reserved) return { ok: false, error: `额外参数里不能设置 ${reserved}，它由 nest 控制` }
  return { ok: true, value: Object.keys(parsed).length ? (parsed as Record<string, unknown>) : null }
}

/** The key to send: only when typed during this edit and non-empty after trimming. */
export function keyToSend(form: ModelFormState): string | null {
  if (form.kind === 'codex' || form.clearKey || !form.keyTouched) return null
  return form.key.trim() || null
}

export type CheckedForm =
  | { ok: true; options: ProviderOptions; key: string | null }
  | { ok: false; errors: ModelFormErrors }

/** Validate everything the backend would, so most mistakes show next to the field before anything is sent. */
export function checkForm(
  form: ModelFormState,
  { creating, takenIds }: { creating: boolean; takenIds: string[] },
): CheckedForm {
  const errors: ModelFormErrors = {}
  if (creating) {
    const id = form.id.trim()
    if (!ID_PATTERN.test(id)) errors.id = '名字只能用小写字母、数字和 -，2–40 个字，不能以 - 开头'
    else if (takenIds.includes(id)) errors.id = `已经有叫 ${id} 的模型`
  }
  const baseUrl = checkBaseUrl(form.base_url, form.kind)
  if (baseUrl) errors.base_url = baseUrl
  const model = form.model.trim()
  if (!model) errors.model = '填写模型名'
  else if (charCount(model) > 100) errors.model = '模型名最多 100 个字'
  const proxy = checkProxy(form.proxy)
  if (proxy) errors.proxy = proxy

  const allowed = KIND_OPTIONS[form.kind]
  const options: ProviderOptions = {}
  const proxyValue = normalizeProxy(form.proxy)
  if (proxyValue) options.proxy = proxyValue
  const timeout = parseNumber(form.timeout, { min: 1, max: 300 })
  if (!timeout.ok) errors.timeout = '超时填 1–300 之间的秒数'
  else if (timeout.value !== null) options.timeout = timeout.value
  if (allowed.includes('temperature')) {
    const temperature = parseNumber(form.temperature, { min: 0, max: 2 })
    if (!temperature.ok) errors.temperature = '温度填 0–2 之间的数，留空用模型默认'
    else if (temperature.value !== null) options.temperature = temperature.value
  }
  if (allowed.includes('extra_body')) {
    const extra = parseExtra(form.extra_body)
    if (!extra.ok) errors.extra_body = extra.error
    else if (extra.value) options.extra_body = extra.value
  }
  if (allowed.includes('input_format')) options.input_format = form.input_format
  if (allowed.includes('auth_header')) options.auth_header = form.auth_header
  if (allowed.includes('effort') && form.effort !== 'low') {
    options.effort = form.effort
    if (options.extra_body && 'output_config' in options.extra_body) {
      errors.extra_body = '这里的 output_config 会盖过思考档，两处留一处'
    }
  }
  if (allowed.includes('auth_file') && form.auth_file.trim()) options.auth_file = form.auth_file.trim()

  const key = keyToSend(form)
  if (key !== null && !KEY_PATTERN.test(key)) errors.key = '密钥里有空格、换行或中文字符，请检查是否复制完整'

  if (Object.keys(errors).length) return { ok: false, errors }
  return { ok: true, options, key }
}

export function createBody(form: ModelFormState, options: ProviderOptions, key: string | null): ProviderCreate {
  return {
    id: form.id.trim(),
    kind: form.kind,
    base_url: form.base_url.trim(),
    model: form.model.trim(),
    options,
    ...(key !== null ? { key } : {}),
  }
}

export function patchBody(form: ModelFormState, options: ProviderOptions, key: string | null): ProviderPatch {
  return {
    kind: form.kind,
    base_url: form.base_url.trim(),
    model: form.model.trim(),
    options,
    ...(key !== null ? { key } : {}),
    ...(form.kind !== 'codex' && form.clearKey ? { clear_key: true } : {}),
  }
}

/** Changes that alter where and how a live bot's requests go (asked about before saving). */
export function changesConnection(form: ModelFormState, provider: ProviderView): boolean {
  return (
    form.kind !== provider.kind ||
    form.base_url.trim() !== provider.base_url ||
    form.model.trim() !== provider.model ||
    normalizeProxy(form.proxy) !== text(provider.options.proxy) ||
    keyToSend(form) !== null ||
    (form.kind !== 'codex' && form.clearKey) ||
    (form.kind === 'codex' && form.auth_file.trim() !== text(provider.options.auth_file)) // another login
  )
}
