import { useCallback, useEffect, useId, useRef, useState, type CSSProperties } from 'react'

import { BotGlyph } from '@/components/BotGlyph'
import { Field } from '@/components/Field'
import { Segmented } from '@/components/Segmented'
import {
  CUSTOM,
  INTERVAL_MAX,
  INTERVAL_MIN,
  INTERVALS,
  KINDS,
  MODES,
  PRIVATE,
  checkForm,
  createBody,
  errorField,
  groupTarget,
  kindOf,
  patchBody,
  startsOver,
  targetLabel,
  type WatchFormErrors,
  type WatchFormField,
  type WatchFormState,
  type WatchValues,
} from '@/components/watches/shared'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  ApiError,
  getJson,
  sendJson,
  type Bot,
  type SystemView,
  type WatchMode,
  type WatchTarget,
  type WatchView,
} from '@/lib/api'
import { CHANGED_ELSEWHERE, type Draft } from '@/lib/drafts'
import { charCount, editKeys } from '@/lib/forms'
import { useLive, usePlatforms, useProviders } from '@/lib/live'
import { botLook } from '@/lib/persona'
import { QQ_LABEL } from '@/lib/platforms'
import { cn } from '@/lib/utils'

const INSTRUCTIONS_MAX = 500

/** Same bot and same chat: what a 推送 confirmation was given for. */
const targetKey = (botId: string, kind: string, id: string) => `${botId}|${kind}|${id}`
const valuesKey = (v: WatchValues) => targetKey(v.bot_id, v.target_kind, v.target_id)

/** Styled like <Label>, for a control group (radios) that a <label> cannot point at. */
function GroupLabel({ id, children }: { id: string; children: string }) {
  return (
    <span id={id} className="flex items-center gap-2 text-sm leading-none font-medium select-none">
      {children}
    </span>
  )
}

/**
 * 添加监控 (watch null) or 编辑. The owner keeps the draft and renders this only while editing; clearing the
 * draft (保存 succeeded, 取消, Esc) closes it.
 */
export function WatchForm({
  watch,
  draft,
  bots,
  onSaved,
}: {
  watch: WatchView | null
  draft: Draft<WatchFormState>
  /** /api/bots, for current names (null until loaded). */
  bots: Bot[] | null
  onSaved: (view: WatchView) => void
}) {
  const form = draft.value
  const { accounts } = useLive()
  const { data: platforms } = usePlatforms()
  const providers = useProviders()
  const [targets, setTargets] = useState<WatchTarget[] | null>(null)
  const [targetsError, setTargetsError] = useState('')
  // null while loading; undefined when unknown (an older nest, or the read failed).
  const [proxy, setProxy] = useState<string | null | undefined>(null)
  const [tried, setTried] = useState(false)
  // Bumped by each 保存 that stopped on a field error, to move focus to the first one.
  const [blocked, setBlocked] = useState(0)
  const [saving, setSaving] = useState(false)
  const [serverError, setServerError] = useState<{ field?: WatchFormField; text: string } | null>(null)
  // The target 推送 was confirmed for; saving a push watch to any other target asks again.
  const [confirmedFor, setConfirmedFor] = useState<string | null>(() =>
    watch?.mode === 'push' ? targetKey(watch.bot_id, watch.target_kind, watch.target_id) : null,
  )
  const [focusCustom, setFocusCustom] = useState(0)
  const container = useRef<HTMLDivElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const customRef = useRef<HTMLInputElement>(null)
  const ids = {
    kind: useId(),
    kindHint: useId(),
    bot: useId(),
    botError: useId(),
    proxy: useId(),
    proxyHint: useId(),
    mode: useId(),
    modeHint: useId(),
  }

  const loadTargets = useCallback(() => {
    getJson<WatchTarget[]>('/api/watches/targets').then(
      (value) => {
        setTargets(value)
        setTargetsError('')
      },
      (e: Error) => setTargetsError(e.message),
    )
  }, [])
  useEffect(loadTargets, [loadTargets])
  useEffect(() => {
    getJson<SystemView>('/api/system').then(
      (system) => setProxy(typeof system.fetch_proxy === 'string' ? system.fetch_proxy : undefined),
      () => setProxy(undefined),
    )
  }, [])
  // Mounted exactly when editing starts: put the cursor in the first field.
  useEffect(() => nameRef.current?.focus(), [])
  useEffect(() => {
    if (blocked) container.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
  }, [blocked])
  useEffect(() => {
    if (focusCustom) customRef.current?.focus()
  }, [focusCustom])

  // ---- Who it goes to: the bot (first one by default) and its group (first one by default) or a 私聊 ----
  const nameOf = (id: string, fallback: string) => bots?.find((b) => b.id === id)?.name ?? fallback
  const botOptions = (targets ?? bots?.map((b) => ({ bot_id: b.id, bot_name: b.name })) ?? []).map((t) => ({
    id: t.bot_id,
    name: nameOf(t.bot_id, t.bot_name),
  }))
  if (watch && !botOptions.some((b) => b.id === watch.bot_id)) {
    botOptions.unshift({ id: watch.bot_id, name: nameOf(watch.bot_id, watch.bot_name) })
  }
  const botId = form.botId || botOptions[0]?.id || ''
  const botName = botOptions.find((b) => b.id === botId)?.name ?? ''
  /** null: not known (the list has not loaded or failed). */
  const groups = targets ? (targets.find((t) => t.bot_id === botId)?.groups ?? []) : null
  const target = form.target || (groups?.[0] ? groupTarget(groups[0].group_id) : groups ? PRIVATE : '')
  const userWord = platforms?.find((p) => p.key === botId.split(':')[0])?.user_label ?? QQ_LABEL
  const groupOptions = [...(groups ?? [])]
  // The saved group stays selectable even when the list does not have it (any more, or not yet).
  const savedGroupMissing =
    watch !== null &&
    watch.target_kind === 'group' &&
    watch.bot_id === botId &&
    !groupOptions.some((g) => g.group_id === watch.target_id)
  if (savedGroupMissing) groupOptions.unshift({ group_id: watch.target_id, name: watch.target_name })
  const targetName =
    target === PRIVATE
      ? `私聊 ${form.privateId.trim() || `（还没填${userWord}）`}`
      : groupOptions.find((g) => groupTarget(g.group_id) === target)?.name || `群 ${target.slice('group:'.length)}`

  const checked = checkForm(form, { botId, target })
  const errors: WatchFormErrors = tried && !checked.ok ? checked.errors : {}
  const errorFor = (field: WatchFormField) =>
    errors[field] ?? (serverError?.field === field ? serverError.text : undefined)
  // Fields on screen right now: a server error naming any other one is shown above the buttons instead.
  const visible = new Set<WatchFormField>(['name', 'url', 'interval', 'proxy', 'bot', 'target', 'provider'])
  if (form.kind !== 'feed') visible.add('selector')
  if (form.kind === 'price') visible.add('threshold')
  if (target === PRIVATE) visible.add('privateId')
  if (form.providerId) visible.add('instructions')
  const generalError = serverError && (!serverError.field || !visible.has(serverError.field)) ? serverError.text : ''

  const update = (patch: Partial<WatchFormState>) => {
    setServerError(null)
    draft.set((current) => ({ ...current, ...patch }))
  }
  const cancel = () => {
    if (!saving) draft.clear()
  }

  const chooseBot = (id: string) => {
    const next = targets?.find((t) => t.bot_id === id)?.groups ?? []
    const keep = target === PRIVATE || next.some((g) => groupTarget(g.group_id) === target)
    update({ botId: id, target: keep ? target : '' })
  }

  const confirmPush = (name: string) => window.confirm(`${botName} 会真的发到「${name}」。继续？`)
  const chooseMode = (mode: WatchMode) => {
    if (mode === 'push') {
      if (!confirmPush(targetName)) return
      const kind = target === PRIVATE ? 'private' : 'group'
      const id = target === PRIVATE ? form.privateId.trim() : target.slice('group:'.length)
      setConfirmedFor(targetKey(botId, kind, id))
    }
    update({ mode })
  }

  const save = async () => {
    if (saving) return
    if (watch && !draft.dirty) {
      draft.clear()
      return
    }
    setTried(true)
    setServerError(null)
    if (!checked.ok) {
      setBlocked((n) => n + 1)
      return
    }
    const values = checked.values
    const body = watch ? patchBody(values, watch) : createBody(values)
    if (watch && Object.keys(body).length === 0) {
      draft.clear()
      return
    }
    if (values.mode === 'push' && valuesKey(values) !== confirmedFor && !confirmPush(targetName)) return
    setSaving(true)
    try {
      const view = watch
        ? await sendJson<WatchView>('PATCH', `/api/watches/${watch.id}`, body)
        : await sendJson<WatchView>('POST', '/api/watches', body)
      draft.clear()
      onSaved(view)
    } catch (e) {
      const text = (e as Error).message
      setServerError({ field: e instanceof ApiError && e.status < 500 ? errorField(text) : undefined, text })
      setSaving(false)
    }
  }

  const kind = kindOf(form.kind)
  const models = providers.data ?? []
  const listed = models.some((p) => p.id === form.providerId)
  const custom = form.interval === CUSTOM
  const intervalError = errorFor('interval')
  const proxyMissing = proxy === ''
  const resets = watch !== null && checked.ok && startsOver(checked.values, watch)
  const hue = botId ? botLook(botName, botId).hue : 'var(--ring)'
  const online = (id: string) => accounts.find((a) => a.id === id)?.status.state === 'connected'

  return (
    <div
      ref={container}
      role="group"
      aria-label={watch ? `编辑监控「${watch.name}」` : '添加监控'}
      style={{ '--hue': hue } as CSSProperties}
      className="space-y-6"
      onKeyDown={editKeys({ onEnter: () => void save(), onEscape: cancel })}
    >
      {draft.changedElsewhere && (
        <p role="status" className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
          {CHANGED_ELSEWHERE}
        </p>
      )}

      <div className="space-y-4">
        <h3 className="text-sm font-semibold">检查什么</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="名字" hint="只在控制台里用，方便你认出它" error={errorFor('name')}>
            {(control) => (
              <Input
                {...control}
                ref={nameRef}
                value={form.name}
                placeholder="例如 少数派新文章"
                autoComplete="off"
                onChange={(e) => update({ name: e.target.value })}
              />
            )}
          </Field>
          <div className="grid content-start gap-1.5">
            <GroupLabel id={ids.kind}>类型</GroupLabel>
            <Segmented
              labelledBy={ids.kind}
              describedBy={ids.kindHint}
              options={KINDS.map((k) => ({ value: k.kind, label: k.label, Icon: k.Icon }))}
              value={form.kind}
              onChange={(next) => update({ kind: next })}
            />
            <p id={ids.kindHint} className="text-xs text-muted-foreground">
              {kind.hint}
            </p>
          </div>
          <Field label="地址" className="sm:col-span-2" hint={kind.urlHint} error={errorFor('url')}>
            {(control) => (
              <Input
                {...control}
                value={form.url}
                placeholder="https://"
                inputMode="url"
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => update({ url: e.target.value })}
              />
            )}
          </Field>
          {form.kind !== 'feed' && (
            <Field
              label={form.kind === 'price' ? '价格在哪里（选择器）' : '看哪一块（选择器）'}
              hint={
                form.kind === 'price'
                  ? '在浏览器里右键元素 → 检查 → 复制选择器'
                  : '留空表示看整个页面。在浏览器里右键元素 → 检查 → 复制选择器'
              }
              error={errorFor('selector')}
            >
              {(control) => (
                <Input
                  {...control}
                  value={form.selector}
                  placeholder={form.kind === 'price' ? '例如 .price-now' : '例如 #notice-list'}
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono text-xs"
                  onChange={(e) => update({ selector: e.target.value })}
                />
              )}
            </Field>
          )}
          {form.kind === 'price' && (
            <Field label="低于这个价格才推送" hint="留空表示价格一变就推送" error={errorFor('threshold')}>
              {(control) => (
                <Input
                  {...control}
                  value={form.threshold}
                  placeholder="例如 199"
                  inputMode="decimal"
                  autoComplete="off"
                  onChange={(e) => update({ threshold: e.target.value })}
                />
              )}
            </Field>
          )}
          <Field
            label="多久检查一次"
            hint={custom ? `${INTERVAL_MIN}–${INTERVAL_MAX} 分钟` : '查得太勤，有的网站会拦下来'}
            error={intervalError}
          >
            {(control) => (
              <div className="flex items-center gap-2">
                <NativeSelect
                  {...control}
                  aria-invalid={intervalError && !custom ? true : undefined}
                  className="min-w-0 flex-1"
                  value={form.interval}
                  onChange={(e) => {
                    const next = e.target.value
                    update({
                      interval: next,
                      customMinutes: next === CUSTOM && !form.customMinutes ? form.interval : form.customMinutes,
                    })
                    if (next === CUSTOM) setFocusCustom((n) => n + 1)
                  }}
                >
                  {INTERVALS.map((i) => (
                    <option key={i.minutes} value={String(i.minutes)}>
                      {i.label}
                    </option>
                  ))}
                  <option value={CUSTOM}>自定义…</option>
                </NativeSelect>
                {custom && (
                  <>
                    <Input
                      ref={customRef}
                      aria-label="每隔多少分钟检查一次"
                      aria-invalid={intervalError ? true : undefined}
                      aria-describedby={control['aria-describedby']}
                      className="w-20"
                      inputMode="numeric"
                      autoComplete="off"
                      value={form.customMinutes}
                      onChange={(e) => update({ customMinutes: e.target.value })}
                    />
                    <span className="text-sm text-muted-foreground">分钟</span>
                  </>
                )}
              </div>
            )}
          </Field>
          <div className="grid content-start gap-1.5">
            <Label htmlFor={ids.proxy}>通过外网代理</Label>
            <div className="flex min-h-8 items-center gap-2.5">
              <Switch
                id={ids.proxy}
                checked={form.useProxy}
                aria-describedby={ids.proxyHint}
                onCheckedChange={(next) => update({ useProxy: next })}
              />
              <p
                id={ids.proxyHint}
                className={cn(
                  'text-xs',
                  proxyMissing && form.useProxy ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground',
                )}
              >
                {proxyMissing
                  ? '先在「系统」页设置外网代理'
                  : proxy
                    ? `用「系统」页设置的 ${proxy}`
                    : '直接打不开的网站才需要'}
              </p>
            </div>
            {errorFor('proxy') && (
              <p role="alert" className="text-xs text-destructive">
                {errorFor('proxy')}
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="space-y-4 border-t pt-5">
        <h3 className="text-sm font-semibold">怎么推送</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="grid content-start gap-2 sm:col-span-2">
            <GroupLabel id={ids.bot}>推送给</GroupLabel>
            {botOptions.length === 0 ? (
              targetsError ? (
                <p role="alert" className="text-sm text-destructive">
                  读取 bot 和群失败：{targetsError}{' '}
                  <button
                    type="button"
                    className="underline underline-offset-2"
                    onClick={() => {
                      setTargetsError('')
                      loadTargets()
                    }}
                  >
                    再试一次
                  </button>
                </p>
              ) : (
                <p role="status" className="text-sm text-muted-foreground">
                  正在读取 bot 和它们所在的群…
                </p>
              )
            ) : (
              <div
                role="radiogroup"
                aria-labelledby={ids.bot}
                aria-describedby={errorFor('bot') ? ids.botError : undefined}
                className="flex flex-wrap gap-2"
              >
                {botOptions.map((b) => {
                  const current = b.id === botId
                  return (
                    <button
                      key={b.id}
                      type="button"
                      role="radio"
                      aria-checked={current}
                      onClick={() => !current && chooseBot(b.id)}
                      style={{ '--hue': botLook(b.name, b.id).hue } as CSSProperties}
                      className={cn(
                        'inline-flex items-center gap-2 rounded-full border py-1 pr-3.5 pl-1 text-sm transition-[background-color,border-color,color,box-shadow] duration-200 active:scale-[0.98]',
                        current
                          ? 'border-[color-mix(in_oklch,var(--hue)_45%,transparent)] bg-[color-mix(in_oklch,var(--hue)_10%,var(--card))] font-medium text-foreground shadow-[var(--shadow-lift)]'
                          : 'text-muted-foreground hover:border-input hover:text-foreground',
                      )}
                    >
                      <BotGlyph name={b.name} id={b.id} size="sm" online={online(b.id)} />
                      {b.name}
                    </button>
                  )
                })}
              </div>
            )}
            {errorFor('bot') && (
              <p id={ids.botError} role="alert" className="text-xs text-destructive">
                {errorFor('bot')}
              </p>
            )}
          </div>
          <Field
            label="发到"
            error={errorFor('target')}
            warning={
              savedGroupMissing && groups !== null ? `${botName} 现在不在这个群里，换一个群或改成私聊` : undefined
            }
            hint={
              targetsError && botOptions.length > 0
                ? '群列表没读到，只能留在原来的群或改成私聊'
                : groups && groups.length === 0
                  ? `没找到 ${botName} 所在的群，可以先发私聊`
                  : '群列表来自这个号收到过消息的群'
            }
          >
            {(control) => (
              <NativeSelect
                {...control}
                className="w-full"
                value={target}
                disabled={!botId}
                onChange={(e) => update({ target: e.target.value })}
              >
                {target === '' && <option value="">{targetsError ? '没读到群列表' : '正在读取群…'}</option>}
                {groupOptions.map((g) => (
                  <option key={g.group_id} value={groupTarget(g.group_id)}>
                    {targetLabel(g)}
                  </option>
                ))}
                <option value={PRIVATE}>私聊（填{userWord}）</option>
              </NativeSelect>
            )}
          </Field>
          {target === PRIVATE && (
            <Field
              label={userWord}
              hint={botId.startsWith('qq:') ? '一般要先和这个 bot 互相加好友，才收得到私聊' : undefined}
              error={errorFor('privateId')}
            >
              {(control) => (
                <Input
                  {...control}
                  value={form.privateId}
                  placeholder={botId.startsWith('qq:') ? '例如 12345678' : ''}
                  autoComplete="off"
                  onChange={(e) => update({ privateId: e.target.value })}
                />
              )}
            </Field>
          )}
          <Field
            label="用模型改写"
            hint={
              form.providerId
                ? '让 bot 用这个模型把更新写成一条自然的消息；写不出来时会用默认格式'
                : '按默认格式列出标题和链接'
            }
            error={errorFor('provider')}
          >
            {(control) => (
              <NativeSelect
                {...control}
                className="w-full"
                value={form.providerId}
                onChange={(e) => update({ providerId: e.target.value })}
              >
                <option value="">不用，直接列出</option>
                {!listed && form.providerId && <option value={form.providerId}>{form.providerId}</option>}
                {models.map((p) => (
                  <option key={p.id} value={p.id}>
                    {`${p.id}（${p.model}）${p.build_error !== null ? '（配置有误）' : ''}`}
                  </option>
                ))}
              </NativeSelect>
            )}
          </Field>
          {form.providerId && (
            <Field
              label="额外要求"
              className="sm:col-span-2"
              hint={`可以不填，最多 ${INSTRUCTIONS_MAX} 字${form.instructions ? `，现在 ${charCount(form.instructions.trim())} 字` : ''}`}
              error={errorFor('instructions')}
            >
              {(control) => (
                <Textarea
                  {...control}
                  rows={2}
                  value={form.instructions}
                  placeholder="例如：口气轻松一点，只说最重要的一条"
                  onChange={(e) => update({ instructions: e.target.value })}
                />
              )}
            </Field>
          )}
          <div className="grid content-start gap-1.5 sm:col-span-2">
            <GroupLabel id={ids.mode}>模式</GroupLabel>
            <Segmented
              labelledBy={ids.mode}
              describedBy={ids.modeHint}
              options={MODES.map((m) => ({ value: m.value, label: m.label, accent: m.value === 'push' }))}
              value={form.mode}
              onChange={chooseMode}
            />
            <p id={ids.modeHint} className="text-xs text-muted-foreground">
              {MODES.find((m) => m.value === form.mode)?.hint}
            </p>
            {form.mode === 'push' && bots?.find((b) => b.id === botId)?.mode === 'observe' && (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                {botName} 平时聊天是「只看不说」，但监控选了「推送」仍会用它的号真的发消息。
              </p>
            )}
          </div>
        </div>
      </div>

      {resets && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          改了类型、地址或选择器：下次检查会重新记下现在的内容，这一次不推送
        </p>
      )}
      {generalError && (
        <p role="alert" className="text-sm text-destructive">
          {generalError}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" disabled={saving} onClick={() => void save()}>
          {saving ? '保存中…' : watch ? '保存' : '添加'}
        </Button>
        <Button type="button" variant="outline" disabled={saving} onClick={cancel}>
          取消
        </Button>
        <span className="text-xs text-muted-foreground">
          {saving
            ? '正在保存，完成前不能取消'
            : watch
              ? '只保存改过的项，保存后立刻生效'
              : '添加后很快会检查第一次；第一次只记下现在的内容，不推送'}
        </span>
      </div>
    </div>
  )
}
