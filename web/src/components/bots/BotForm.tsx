import { useEffect, useRef, useState, type Ref } from 'react'

import {
  ALIAS_LIMITS,
  ONE_CHAR,
  aliasProblem,
  botForm,
  nameProblem,
  parseAliases,
  sameList,
  type BotFormState,
} from '@/components/bots/shared'
import { Field } from '@/components/Field'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { Textarea } from '@/components/ui/textarea'
import { sendJson, type Bot, type BotPatch, type Ok, type ProviderView } from '@/lib/api'
import { CHANGED_ELSEWHERE, type Draft } from '@/lib/drafts'
import { charCount, editKeys } from '@/lib/forms'
import { kindLabel } from '@/lib/models'
import { cn } from '@/lib/utils'

/**
 * 显示名 · 别名 · 模型 for one bot. The card owns the draft and renders this only while editing; clearing the
 * draft (保存 succeeded, 取消, Esc) closes it.
 */
export function BotForm({
  bot,
  providers,
  draft,
  firstField,
  onSaved,
}: {
  bot: Bot
  /** null until the model list has loaded. */
  providers: ProviderView[] | null
  draft: Draft<BotFormState>
  firstField: Ref<HTMLInputElement>
  onSaved: () => void
}) {
  const form = draft.value
  const live = bot.mode === 'live'
  const [tried, setTried] = useState(false)
  // Bumped by each 保存 that stopped on a field error, to move focus to the first one.
  const [blocked, setBlocked] = useState(0)
  const [saving, setSaving] = useState(false)
  const [serverError, setServerError] = useState('')
  const container = useRef<HTMLDivElement>(null)

  const remove = async () => {
    const question =
      ((bot.platform ?? 'qq') === 'qq'
        ? `删除 ${bot.name}？它会从这里消失、不再连 QQ，它的 QQ 容器也会删掉。` +
          '聊天记录、判断记录、用量和 QQ 登录数据都保留：以后用同一个 QQ 号再「增加 bot」，就能接着用，一般不用重新扫码。'
        : `删除 ${bot.name}？它会从这里消失、不再连接，保存的密钥也会删掉。` +
          '聊天记录、判断记录和用量都保留：以后再「增加 bot」填同一个账号，就能接着用。')
    if (!window.confirm(question)) return
    setSaving(true)
    setServerError('')
    try {
      const done = await sendJson<{ ok: boolean; problem: string }>('DELETE', `/api/bots/${encodeURIComponent(bot.id)}`)
      if (done.problem) window.alert(done.problem)
      onSaved()
    } catch (e) {
      setServerError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    if (blocked) container.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
  }, [blocked])

  const name = form.name.trim()
  const aliases = parseAliases(form.aliases)
  const providerId = form.providerId || null
  const nameChanged = name !== bot.name
  // Untouched, the field is only the stored list joined for display: re-splitting it must not count as a change
  // (an alias set from the command line may itself contain a comma).
  const aliasesTouched = form.aliases !== botForm(bot).aliases
  const aliasesChanged = aliasesTouched && !sameList(aliases, bot.aliases)
  const shownAliases = aliasesTouched ? aliases : bot.aliases
  const providerChanged = providerId !== bot.provider_id
  const backupId = form.backupId || null
  const backupChanged = backupId !== (bot.backup_provider_id ?? null)
  // Only what is sent is checked, so an old value set from the command line never blocks another change.
  const nameError = nameChanged ? nameProblem(form.name) : null
  const aliasErrors = aliasesTouched ? aliases.map(aliasProblem) : []
  const aliasError = aliasesChanged ? (aliasErrors.find(Boolean) ?? null) : null
  const models = providers ?? []
  const listed = models.some((p) => p.id === form.providerId)
  const broken = models.some((p) => p.build_error !== null)

  const update = (patch: Partial<BotFormState>) => {
    setServerError('')
    draft.set((current) => ({ ...current, ...patch }))
  }
  const cancel = () => {
    if (!saving) draft.clear()
  }

  const save = async () => {
    if (saving) return
    const body: BotPatch = {}
    if (nameChanged) body.name = name
    if (aliasesChanged) body.aliases = aliases
    if (providerChanged) body.provider_id = providerId
    if (backupChanged) body.backup_provider_id = backupId
    if (Object.keys(body).length === 0) {
      draft.clear()
      return
    }
    setTried(true)
    setServerError('')
    if (nameError || aliasError) {
      setBlocked((n) => n + 1)
      return
    }
    if (
      providerChanged &&
      live &&
      !window.confirm(`${bot.name} 正在正式发言，换成模型 ${providerId} 后立刻生效。继续？`)
    ) {
      return
    }
    setSaving(true)
    try {
      await sendJson<Ok>('PATCH', `/api/bots/${encodeURIComponent(bot.id)}`, body)
      draft.clear()
      onSaved()
    } catch (e) {
      setServerError((e as Error).message)
      setSaving(false)
    }
  }

  const modelHint = [
    live ? '正式发言时必须有模型，先改成只看不说或暂停' : '',
    live && broken ? '标着（配置有误）的模型要先在「模型」页修好' : '',
    providers && models.length === 0 ? '还没有模型，先到「模型」页添加' : '',
  ]
    .filter(Boolean)
    .join('；')

  return (
    <div
      ref={container}
      role="group"
      aria-label={`编辑 ${bot.name}`}
      className="space-y-4 rounded-lg border p-3"
      onKeyDown={editKeys({ onEnter: () => void save(), onEscape: cancel })}
    >
      {draft.changedElsewhere && (
        <p role="status" className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
          {CHANGED_ELSEWHERE}
        </p>
      )}
      <Field
        label="显示名"
        hint="只改 nest 里的称呼（提示词、点名、控制台），不改平台上的昵称"
        warning={charCount(name) === 1 ? ONE_CHAR : undefined}
        error={nameError && (tried || name) ? nameError : undefined}
      >
        {(control) => (
          <Input
            {...control}
            ref={firstField}
            value={form.name}
            autoComplete="off"
            onChange={(e) => update({ name: e.target.value })}
          />
        )}
      </Field>
      <div className="space-y-2">
        <Field
          label="别名"
          hint="用「、」「，」或换行分开；显示名本身也算点名"
          warning={shownAliases.some((a) => charCount(a) === 1) ? ONE_CHAR : undefined}
          error={aliasError ?? undefined}
        >
          {(control) => (
            <Textarea
              {...control}
              rows={2}
              value={form.aliases}
              placeholder="例如 鲸鲸、胖鲸"
              onChange={(e) => update({ aliases: e.target.value })}
            />
          )}
        </Field>
        <ul aria-label="会保存这些别名" className="flex flex-wrap gap-1.5">
          {shownAliases.length === 0 ? (
            <li className="text-xs text-muted-foreground">没有别名，只认显示名</li>
          ) : (
            shownAliases.map((alias, i) => (
              <li
                key={`${i}:${alias}`}
                className={cn(
                  'rounded-full border px-2 py-0.5 text-xs whitespace-pre',
                  aliasErrors[i] ? 'border-destructive/50 bg-destructive/10 text-destructive' : 'bg-muted',
                )}
              >
                {alias}
                {aliasErrors[i] === ALIAS_LIMITS && <span className="sr-only">（{ALIAS_LIMITS}）</span>}
              </li>
            ))
          )}
        </ul>
      </div>
      <Field label="模型" hint={modelHint || undefined}>
        {(control) => (
          <NativeSelect
            {...control}
            className="w-full"
            value={form.providerId}
            onChange={(e) => update({ providerId: e.target.value })}
          >
            <option value="" disabled={live}>
              不使用模型
            </option>
            {!listed && form.providerId && <option value={form.providerId}>{form.providerId}</option>}
            {models.map((p) => (
              <option key={p.id} value={p.id} disabled={live && p.build_error !== null}>
                {`${p.id}（${kindLabel(p.kind)}）${p.build_error !== null ? '（配置有误）' : ''}`}
              </option>
            ))}
          </NativeSelect>
        )}
      </Field>
      <Field label="备用模型" hint="上面的模型额度用完或连续失败时改用它，判断记录里会写明">
        {(control) => (
          <NativeSelect
            {...control}
            className="w-full"
            value={form.backupId}
            onChange={(e) => update({ backupId: e.target.value })}
          >
            <option value="">不用备用模型</option>
            {models
              .filter((p) => p.id !== form.providerId)
              .map((p) => (
                <option key={p.id} value={p.id} disabled={p.build_error !== null}>
                  {`${p.id}（${kindLabel(p.kind)}）${p.build_error !== null ? '（配置有误）' : ''}`}
                </option>
              ))}
          </NativeSelect>
        )}
      </Field>
      {serverError && (
        <p role="alert" className="text-sm text-destructive">
          {serverError}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" disabled={saving} onClick={() => void save()}>
          {saving ? '保存中…' : '保存'}
        </Button>
        <Button type="button" variant="outline" disabled={saving} onClick={cancel}>
          取消
        </Button>
        <span className="text-xs text-muted-foreground">
          {saving ? '正在保存，完成前不能取消' : '只保存改过的项，保存后立刻生效'}
        </span>
        <Button
          type="button"
          variant="ghost"
          className="ml-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
          disabled={saving}
          onClick={() => void remove()}
        >
          删除这个 bot
        </Button>
      </div>
    </div>
  )
}
