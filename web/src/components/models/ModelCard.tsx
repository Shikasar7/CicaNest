import { useState, type ReactNode } from 'react'

import { ResultText, type Result } from '@/components/Feedback'
import { ModelHealthBadge } from '@/components/ModelHealthBadge'
import { ModelForm } from '@/components/models/ModelForm'
import { formFrom, sameForm } from '@/components/models/form'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { sendJson, type BotMode, type Ok, type ProviderTestResult, type ProviderView } from '@/lib/api'
import { useDraft, useEditFocus } from '@/lib/drafts'
import { formatAgo, formatLatency } from '@/lib/format'
import { useLive } from '@/lib/live'
import { CODEX_AUTH_FILE, EFFORT_LABEL, effortOf, kindLabel } from '@/lib/models'

const MODE_LABEL: Record<BotMode, string> = { observe: '只看不说', live: '正式发言', paused: '已暂停' }

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  )
}

function KeyStatus({ provider }: { provider: ProviderView }) {
  if (provider.kind === 'codex') {
    const authFile = typeof provider.options.auth_file === 'string' ? provider.options.auth_file : ''
    return <>用登录文件 {authFile || CODEX_AUTH_FILE}</>
  }
  return (
    <>
      {provider.key_from_env ? (
        '由系统环境变量提供（在这里改不会生效）'
      ) : provider.key_set ? (
        '密钥已设置'
      ) : (
        <span className="text-muted-foreground">没有密钥</span>
      )}
      {provider.key_shared_with.length > 0 && (
        <span className="block text-xs text-muted-foreground">
          这个密钥也给 {provider.key_shared_with.join('、')} 用，改了会一起变
        </span>
      )}
    </>
  )
}

/** A stored number; anything else (e.g. null written from the command line) is shown as it is and flagged. */
function Stored({ value, unit = '' }: { value: unknown; unit?: string }) {
  if (typeof value === 'number' && Number.isFinite(value)) return <>{`${value}${unit}`}</>
  return <span className="text-destructive">{JSON.stringify(value) ?? String(value)}（格式不对，编辑后保存一次）</span>
}

function OptionRows({ provider }: { provider: ProviderView }) {
  const { options } = provider
  const proxy = typeof options.proxy === 'string' ? options.proxy : ''
  const extra = options.extra_body
  return (
    <>
      <Row label="代理">{proxy || <span className="text-muted-foreground">不用代理</span>}</Row>
      <Row label="超时">
        {options.timeout === undefined ? (
          <span className="text-muted-foreground">40 秒（默认）</span>
        ) : (
          <Stored value={options.timeout} unit=" 秒" />
        )}
      </Row>
      {provider.kind === 'openai_chat' && (
        <Row label="温度">
          {options.temperature === undefined ? (
            <span className="text-muted-foreground">模型默认</span>
          ) : (
            <Stored value={options.temperature} />
          )}
        </Row>
      )}
      {provider.kind === 'openai_responses' && (
        <Row label="对话格式">{options.input_format === 'messages' ? '按角色分条发送' : '合成一段文字'}</Row>
      )}
      {provider.kind === 'anthropic' && (
        <Row label="密钥请求头">{options.auth_header === 'bearer' ? 'Authorization: Bearer' : 'x-api-key'}</Row>
      )}
      {provider.kind === 'anthropic' && <Row label="思考档">{EFFORT_LABEL[effortOf(options.effort)]}</Row>}
      {extra !== undefined && extra !== null && (
        <Row label="额外参数">
          <code className="text-xs break-all">{JSON.stringify(extra)}</code>
        </Row>
      )}
    </>
  )
}

/** One model: its settings, how it is doing, 测试一下 · 编辑 · 删除, and the inline edit form. */
export function ModelCard({
  provider,
  takenIds,
  sharerLive,
  justSaved,
  onSaved,
  onDeleted,
}: {
  provider: ProviderView
  takenIds: string[]
  /** Live bots on the other models that share this model's key. */
  sharerLive: string[]
  /** Saved a moment ago (here or via 添加模型): offer 测试一下. */
  justSaved: boolean
  onSaved: (view: ProviderView) => void
  onDeleted: () => void
}) {
  const { refreshConfig } = useLive()
  const draft = useDraft(formFrom(provider), sameForm)
  const { editButton } = useEditFocus(draft.editing)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<Result | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const id = provider.id
  const users = provider.used_by.map((u) => u.name).join('、')

  const test = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const r = await sendJson<ProviderTestResult>('POST', `/api/providers/${encodeURIComponent(id)}/test`)
      setTestResult(
        r.ok
          ? { ok: true, text: `回复「${r.text}」，用时 ${formatLatency(r.latency_ms)}` }
          : { ok: false, text: `失败：${r.error}` },
      )
    } catch (e) {
      setTestResult({ ok: false, text: `失败：${(e as Error).message}` })
    } finally {
      setTesting(false)
      refreshConfig() // the test updates health but publishes no event
    }
  }

  const remove = async () => {
    if (!window.confirm(`删除模型「${id}」？删除后要重新添加才能再用。`)) return
    setDeleting(true)
    setDeleteError('')
    try {
      await sendJson<Ok>('DELETE', `/api/providers/${encodeURIComponent(id)}`)
      onDeleted()
    } catch (e) {
      setDeleteError(`删除失败：${(e as Error).message}`)
      setDeleting(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <span className="break-all">{id}</span>
          <Badge variant="secondary">{kindLabel(provider.kind)}</Badge>
        </CardTitle>
        <CardDescription className="break-all">{provider.model}</CardDescription>
        {!provider.build_error && (
          <CardAction>
            <ModelHealthBadge health={provider.health} />
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {provider.build_error && (
          <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            配置有误：{provider.build_error}
          </p>
        )}
        {draft.editing ? (
          <ModelForm
            provider={provider}
            takenIds={takenIds}
            sharerLive={sharerLive}
            draft={draft}
            onSaved={(view) => {
              setTestResult(null)
              onSaved(view)
            }}
          />
        ) : (
          <>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
              <Row label="地址">{provider.base_url}</Row>
              <OptionRows provider={provider} />
              <Row label="密钥">
                <KeyStatus provider={provider} />
              </Row>
              <Row label="用在">
                {provider.used_by.length === 0 ? (
                  <span className="text-muted-foreground">还没有 bot 在用</span>
                ) : (
                  provider.used_by.map((u) => `${u.name}（${MODE_LABEL[u.mode]}）`).join('、')
                )}
              </Row>
            </dl>
            {provider.health && (provider.health.last_error || provider.health.last_ok_at) && (
              <div className="space-y-0.5 text-xs text-muted-foreground">
                {provider.health.last_error && <p className="break-words">最近一次失败：{provider.health.last_error}</p>}
                {provider.health.last_ok_at && <p>最近一次成功：{formatAgo(provider.health.last_ok_at)}</p>}
              </div>
            )}
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant={justSaved && !testResult ? 'default' : 'outline'}
                  disabled={testing}
                  onClick={() => void test()}
                >
                  {testing ? '测试中…' : '测试一下'}
                </Button>
                <Button ref={editButton} size="sm" variant="outline" onClick={draft.start}>
                  编辑
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={deleting || provider.used_by.length > 0}
                  onClick={() => void remove()}
                >
                  {deleting ? '删除中…' : '删除'}
                </Button>
              </div>
              {justSaved && !testResult && !testing && (
                <p role="status" className="text-xs text-muted-foreground">
                  已保存，新设置已经生效。点「测试一下」确认能用。
                </p>
              )}
              <ResultText result={testResult} />
              {provider.used_by.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  还有 bot 在用，不能删除：{users}。先在「Bot」页给它们换个模型。
                </p>
              )}
              {deleteError && (
                <p role="alert" className="text-xs text-destructive">
                  {deleteError}
                </p>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
