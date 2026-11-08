import { useEffect, useId, useRef, useState } from 'react'

import { Field } from '@/components/Field'
import {
  changesConnection,
  checkForm,
  createBody,
  keyToSend,
  patchBody,
  withKind,
  type ModelFormErrors,
  type ModelFormField,
  type ModelFormState,
} from '@/components/models/form'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Textarea } from '@/components/ui/textarea'
import {
  ApiError,
  sendJson,
  type ProviderKind,
  type ProviderModels,
  type ProviderOptions,
  type ProviderView,
} from '@/lib/api'
import { CHANGED_ELSEWHERE, type Draft } from '@/lib/drafts'
import { editKeys } from '@/lib/forms'
import {
  CODEX_AUTH_FILE,
  MODEL_KINDS,
  OPTION_LABEL,
  isInsecureRemote,
  liveUsers,
  unusedOptions,
  urlHost,
} from '@/lib/models'

const KIND_HINT: Record<ProviderKind, string> = {
  openai_chat: '大多数接口都是这种，例如 DeepSeek 官方、本地代理',
  openai_responses: '例如火山方舟的 Responses 接口',
  anthropic: 'Claude 官方接口，默认思考强度 low，回得快；想改就在额外参数里写 output_config',
  codex: '用这台电脑上 Codex 的登录，不用密钥',
}

const URL_HINT: Record<ProviderKind, string> = {
  openai_chat: '填到 /chat/completions 前面那一段，例如 https://api.deepseek.com/v1',
  openai_responses: '填到 /responses 前面那一段',
  anthropic: '填到 /messages 前面那一段，官方是 https://api.anthropic.com/v1，中转站填它给的地址',
  codex: 'Codex 订阅只能用这个地址',
}

/**
 * The two questions asked before an edit that matters right away. `sharerLive`: live bots on the other models
 * that read the same key, which a new or cleared key reaches too.
 */
function confirmEdit(form: ModelFormState, provider: ProviderView, key: string | null, sharerLive: string[]): boolean {
  const oldHost = urlHost(provider.base_url)
  const newHost = urlHost(form.base_url)
  const savedKeyGoes = provider.key_set && form.kind !== 'codex' && !form.clearKey && key === null
  if (savedKeyGoes && oldHost && newHost && oldHost !== newHost) {
    if (!window.confirm(`已保存的密钥会发给新地址 ${newHost}，确定吗？`)) return false
  }
  const own = changesConnection(form, provider) ? liveUsers(provider) : []
  const sharing = form.kind !== 'codex' && (key !== null || form.clearKey) ? sharerLive : []
  const live = [...new Set([...own, ...sharing])]
  if (live.length > 0) {
    const what = sharing.length > 0 ? '正在用这个模型或共用这个密钥' : '正在用这个模型'
    if (!window.confirm(`正式发言的 ${live.join('、')} ${what}，保存后立刻生效。继续？`)) return false
  }
  return true
}

/**
 * 添加模型 (provider null) or 编辑. The owner keeps the draft and renders this only while editing; clearing the
 * draft (保存 succeeded, 取消, Esc) closes it and forgets the typed key.
 */
export function ModelForm({
  provider,
  takenIds,
  sharerLive = [],
  draft,
  onSaved,
}: {
  provider: ProviderView | null
  takenIds: string[]
  /** Live bots on the other models that share this model's key. */
  sharerLive?: string[]
  draft: Draft<ModelFormState>
  onSaved: (view: ProviderView) => void
}) {
  const creating = provider === null
  const form = draft.value
  const [tried, setTried] = useState(false)
  // Bumped by each 保存 that stopped on a field error, to move focus to the first one.
  const [blocked, setBlocked] = useState(0)
  const [saving, setSaving] = useState(false)
  const [serverError, setServerError] = useState<{ field?: ModelFormField; text: string } | null>(null)
  // 获取列表: the names the interface offered, and what to say about the last try.
  const [models, setModels] = useState<string[]>([])
  const [listing, setListing] = useState<{ busy: boolean; text?: string; failed?: boolean }>({ busy: false })
  const keyName = `nest-model-key-${useId()}`
  const clearId = useId()
  const container = useRef<HTMLDivElement>(null)
  const firstInput = useRef<HTMLInputElement>(null)
  const firstSelect = useRef<HTMLSelectElement>(null)

  useEffect(() => {
    // Mounted exactly when editing starts: put the cursor in the first field.
    ;(creating ? firstInput.current : firstSelect.current)?.focus()
  }, [creating])

  useEffect(() => {
    if (blocked) container.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
  }, [blocked])

  const checked = checkForm(form, { creating, takenIds })
  const errors: ModelFormErrors = tried && !checked.ok ? checked.errors : {}
  const errorFor = (field: ModelFormField) =>
    errors[field] ?? (serverError?.field === field ? serverError.text : undefined)
  const update = (patch: Partial<ModelFormState>) => {
    setServerError(null)
    draft.set((current) => ({ ...current, ...patch }))
  }
  const cancel = () => {
    if (!saving) draft.clear()
  }

  const fetchModels = async () => {
    setListing({ busy: true })
    const options: ProviderOptions = {}
    if (form.proxy.trim()) options.proxy = form.proxy.trim()
    if (form.kind === 'anthropic') options.auth_header = form.auth_header
    try {
      const result = await sendJson<ProviderModels>('POST', '/api/providers/models', {
        kind: form.kind,
        base_url: form.base_url,
        provider_id: provider?.id,
        key: keyToSend(form) ?? undefined,
        options,
      })
      if (!result.ok) {
        setListing({ busy: false, text: `获取失败：${result.error}`, failed: true })
        return
      }
      setModels(result.models)
      setListing({
        busy: false,
        text: result.models.length ? `找到 ${result.models.length} 个模型，在下面选一个` : '这个接口没有返回任何模型',
      })
    } catch (e) {
      setListing({ busy: false, text: `获取失败：${(e as Error).message}`, failed: true })
    }
  }

  const save = async () => {
    if (saving) return
    // Nothing to save: close without a request. A model that failed to load, or whose stored options would be
    // cleaned up, is still saved, since saving is what rebuilds it.
    const unchanged =
      provider &&
      !draft.dirty &&
      !provider.build_error &&
      checked.ok &&
      JSON.stringify(checked.options) === JSON.stringify(provider.options)
    if (unchanged) {
      draft.clear()
      return
    }
    setTried(true)
    setServerError(null)
    if (!checked.ok) {
      setBlocked((n) => n + 1)
      return
    }
    if (provider && !confirmEdit(form, provider, checked.key, sharerLive)) return
    setSaving(true)
    try {
      const view = provider
        ? await sendJson<ProviderView>(
            'PATCH',
            `/api/providers/${encodeURIComponent(provider.id)}`,
            patchBody(form, checked.options, checked.key),
          )
        : await sendJson<ProviderView>('POST', '/api/providers', createBody(form, checked.options, checked.key))
      draft.clear()
      onSaved(view)
    } catch (e) {
      const text = (e as Error).message
      const duplicate = creating && e instanceof ApiError && e.status === 409 && text.startsWith('已经有叫')
      setServerError(duplicate ? { field: 'id', text } : { text })
      setSaving(false)
    }
  }

  const fromEnv = provider?.key_from_env ?? false
  const dropped = provider ? unusedOptions(provider.options, form.kind).map((key) => OPTION_LABEL[key] ?? key) : []
  // Codex uses no key, so saving deletes this model's own saved key (a shared one stays for the others).
  const keyDropped =
    provider !== null &&
    provider.kind !== 'codex' &&
    form.kind === 'codex' &&
    provider.key_set &&
    !fromEnv &&
    provider.key_shared_with.length === 0
  const keyHint = [
    fromEnv
      ? '由系统环境变量提供（在这里改不会生效）'
      : provider
        ? provider.key_set
          ? '现在：密钥已设置'
          : '现在：没有密钥'
        : '本地接口不需要密钥时留空',
    provider && provider.key_shared_with.length > 0
      ? `这个密钥也给 ${provider.key_shared_with.join('、')} 用，改了会一起变`
      : '',
  ]
    .filter(Boolean)
    .join('。')

  return (
    <div
      ref={container}
      role="group"
      aria-label={provider ? `编辑模型 ${provider.id}` : '添加模型'}
      className="space-y-4"
      onKeyDown={editKeys({ onEnter: () => void save(), onEscape: cancel })}
    >
      {draft.changedElsewhere && (
        <p role="status" className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
          {CHANGED_ELSEWHERE}
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        {provider ? (
          <Field label="名字" hint="保存后不能改">
            {(control) => <Input {...control} value={provider.id} readOnly />}
          </Field>
        ) : (
          <Field label="名字" hint="小写字母、数字和 -，2–40 个字，保存后不能改" error={errorFor('id')}>
            {(control) => (
              <Input
                {...control}
                ref={firstInput}
                value={form.id}
                placeholder="例如 deepseek"
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => update({ id: e.target.value })}
              />
            )}
          </Field>
        )}
        <Field label="接口类型" hint={KIND_HINT[form.kind]}>
          {(control) => (
            <NativeSelect
              {...control}
              ref={firstSelect}
              className="w-full"
              value={form.kind}
              onChange={(e) => {
                setServerError(null)
                const kind = e.target.value as ProviderKind
                draft.set((current) => withKind(current, kind))
              }}
            >
              {MODEL_KINDS.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
            </NativeSelect>
          )}
        </Field>
        <Field
          label="地址"
          className="sm:col-span-2"
          hint={URL_HINT[form.kind]}
          warning={isInsecureRemote(form.base_url) ? '这个地址不加密，密钥会明文传输' : undefined}
          error={errorFor('base_url')}
        >
          {(control) => (
            <Input
              {...control}
              value={form.base_url}
              placeholder="https://"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => update({ base_url: e.target.value })}
            />
          )}
        </Field>
        <Field
          label="模型名"
          hint={
            listing.failed
              ? undefined
              : (listing.text ??
                (form.kind === 'codex'
                  ? '接口文档里的模型 ID'
                  : '接口文档里的模型 ID，例如 deepseek-chat；也可以点「获取列表」，用上面的地址和密钥去问'))
          }
          warning={listing.failed ? listing.text : undefined}
          error={errorFor('model')}
        >
          {(control) => (
            <div className="grid gap-2">
              <div className="flex gap-2">
                <Input
                  {...control}
                  className="flex-1"
                  value={form.model}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(e) => update({ model: e.target.value })}
                />
                {form.kind !== 'codex' && (
                  <Button
                    type="button"
                    variant="outline"
                    className="shrink-0"
                    disabled={listing.busy || !form.base_url.trim()}
                    onClick={() => void fetchModels()}
                  >
                    {listing.busy ? '获取中…' : '获取列表'}
                  </Button>
                )}
              </div>
              {models.length > 0 && form.kind !== 'codex' && (
                <NativeSelect
                  aria-label="从获取到的模型里选"
                  className="w-full"
                  value={models.includes(form.model) ? form.model : ''}
                  onChange={(e) => e.target.value && update({ model: e.target.value })}
                >
                  <option value="">从 {models.length} 个模型里选…</option>
                  {models.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </NativeSelect>
              )}
            </div>
          )}
        </Field>
        <Field label="代理" hint="留空表示不用代理" error={errorFor('proxy')}>
          {(control) => (
            <Input
              {...control}
              value={form.proxy}
              placeholder="http://127.0.0.1:7890"
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => update({ proxy: e.target.value })}
            />
          )}
        </Field>
        <Field label="超时（秒）" hint="1–300，留空用默认" error={errorFor('timeout')}>
          {(control) => (
            <Input
              {...control}
              value={form.timeout}
              placeholder="默认 40"
              inputMode="decimal"
              autoComplete="off"
              onChange={(e) => update({ timeout: e.target.value })}
            />
          )}
        </Field>
        {form.kind === 'openai_chat' && (
          <Field label="温度" hint="0–2，留空用模型默认" error={errorFor('temperature')}>
            {(control) => (
              <Input
                {...control}
                value={form.temperature}
                placeholder="模型默认"
                inputMode="decimal"
                autoComplete="off"
                onChange={(e) => update({ temperature: e.target.value })}
              />
            )}
          </Field>
        )}
        {form.kind === 'openai_responses' && (
          <Field label="对话格式" hint="聊天记录怎样交给模型">
            {(control) => (
              <NativeSelect
                {...control}
                className="w-full"
                value={form.input_format}
                onChange={(e) => update({ input_format: e.target.value as ModelFormState['input_format'] })}
              >
                <option value="text">合成一段文字（默认）</option>
                <option value="messages">按角色分条发送</option>
              </NativeSelect>
            )}
          </Field>
        )}
        {form.kind === 'anthropic' && (
          <Field label="密钥请求头" hint="官方用 x-api-key；有的中转站要 Authorization: Bearer">
            {(control) => (
              <NativeSelect
                {...control}
                className="w-full"
                value={form.auth_header}
                onChange={(e) => update({ auth_header: e.target.value as ModelFormState['auth_header'] })}
              >
                <option value="x-api-key">x-api-key（官方，默认）</option>
                <option value="bearer">Authorization: Bearer</option>
              </NativeSelect>
            )}
          </Field>
        )}
        {form.kind === 'anthropic' && (
          <Field label="思考档" hint="低最快，但常跳过思考，偶尔会把“该怎么接”写进回复；中和高回复前先想一下，稍慢，也多用一点 token">
            {(control) => (
              <NativeSelect
                {...control}
                className="w-full"
                value={form.effort}
                onChange={(e) => update({ effort: e.target.value as ModelFormState['effort'] })}
              >
                <option value="low">低（默认）</option>
                <option value="medium">中</option>
                <option value="high">高</option>
              </NativeSelect>
            )}
          </Field>
        )}
        {form.kind === 'codex' && (
          <Field label="登录文件" hint="Codex 登录后生成的文件，留空用默认位置">
            {(control) => (
              <Input
                {...control}
                value={form.auth_file}
                placeholder={CODEX_AUTH_FILE}
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => update({ auth_file: e.target.value })}
              />
            )}
          </Field>
        )}
        {form.kind !== 'codex' && (
          <Field
            label="额外参数"
            className="sm:col-span-2"
            hint="JSON 对象，会合并到每次请求里，留空表示没有。这里的内容会明文显示，不要放密钥"
            error={errorFor('extra_body')}
          >
            {(control) => (
              <Textarea
                {...control}
                rows={3}
                className="font-mono text-xs"
                value={form.extra_body}
                placeholder='{"top_p": 0.9}'
                spellCheck={false}
                onChange={(e) => update({ extra_body: e.target.value })}
              />
            )}
          </Field>
        )}
        {form.kind !== 'codex' && (
          <div className="space-y-2 sm:col-span-2">
            <Field label="密钥" hint={keyHint} error={errorFor('key')}>
              {(control) => (
                <Input
                  {...control}
                  type="password"
                  name={keyName}
                  autoComplete="new-password"
                  spellCheck={false}
                  data-1p-ignore
                  data-lpignore="true"
                  placeholder={provider ? '留空表示不修改' : '留空表示不用密钥'}
                  value={form.key}
                  disabled={fromEnv || form.clearKey}
                  onChange={(e) => update({ key: e.target.value, keyTouched: true })}
                />
              )}
            </Field>
            {provider && (provider.key_set || fromEnv) && (
              <div className="flex items-center gap-2">
                <input
                  id={clearId}
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={form.clearKey}
                  disabled={fromEnv}
                  onChange={(e) => update({ clearKey: e.target.checked, key: '', keyTouched: false })}
                />
                <Label htmlFor={clearId} className="font-normal">
                  清除已保存的密钥
                </Label>
              </div>
            )}
          </div>
        )}
      </div>
      {dropped.length > 0 && (
        <p className="text-xs text-amber-700 dark:text-amber-400">保存后会去掉不适用的设置：{dropped.join('、')}</p>
      )}
      {keyDropped && (
        <p className="text-xs text-amber-700 dark:text-amber-400">保存后会删除已保存的密钥，以后换回这种接口要重新填</p>
      )}
      {serverError && !serverError.field && (
        <p role="alert" className="text-sm text-destructive">
          {serverError.text}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" disabled={saving} onClick={() => void save()}>
          {saving ? '保存中…' : provider ? '保存' : '添加'}
        </Button>
        <Button type="button" variant="outline" disabled={saving} onClick={cancel}>
          取消
        </Button>
        <span className="text-xs text-muted-foreground">
          {saving ? '正在保存，完成前不能取消' : '保存后立刻生效，不用重启'}
        </span>
      </div>
    </div>
  )
}
