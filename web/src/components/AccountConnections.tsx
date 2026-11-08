import { useState, type FormEvent } from 'react'
import { Check, LoaderCircle, X } from 'lucide-react'

import { Field } from '@/components/Field'
import { StatusDot, type Tone } from '@/components/StatusDot'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  sendJson,
  type ConnectionCheck,
  type ConnectionTrial,
  type PlatformView,
  type QQConnection,
  type QQConnectionChange,
} from '@/lib/api'
import { useLive, usePlatforms } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import { cn } from '@/lib/utils'

const path = (id: string) => `/api/accounts/${encodeURIComponent(id)}/connection`

function tone(state: QQConnection['state']): Tone {
  if (state === 'connected') return 'ok'
  if (state === 'connecting' || state === 'reconnecting') return 'wait'
  return state === 'stopped' ? 'off' : 'bad'
}

function CheckLine({ label, result }: { label: string; result: { ok: boolean; detail: string } }) {
  return (
    <p className={cn('flex items-start gap-1.5 text-xs', result.ok ? 'text-ok' : 'text-destructive')}>
      {result.ok ? <Check className="mt-px size-3.5 shrink-0" /> : <X className="mt-px size-3.5 shrink-0" />}
      <span>
        <span className="text-muted-foreground">{label}：</span>
        {result.detail}
      </span>
    </p>
  )
}

/** Only what the user changed; tokens left empty are kept as saved. */
function changes(connection: QQConnection, form: Record<keyof QQConnectionChange, string>): QQConnectionChange {
  const body: QQConnectionChange = {}
  if (form.http_url.trim() !== connection.http_url) body.http_url = form.http_url
  if (form.ws_url.trim() !== connection.ws_url) body.ws_url = form.ws_url
  if (form.http_token) body.http_token = form.http_token
  if (form.ws_token) body.ws_token = form.ws_token
  return body
}

function Editor({ connection, onClose, onSaved }: { connection: QQConnection; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ http_url: connection.http_url, ws_url: connection.ws_url, http_token: '', ws_token: '' })
  const [busy, setBusy] = useState<'' | 'test' | 'save'>('')
  const [error, setError] = useState('')
  const [check, setCheck] = useState<ConnectionCheck | null>(null)
  const set = (key: keyof typeof form) => (value: string) => {
    setForm((f) => ({ ...f, [key]: value }))
    setCheck(null)
  }
  const body = changes(connection, form)
  const dirty = Object.keys(body).length > 0

  const test = async () => {
    setBusy('test')
    setError('')
    setCheck(null)
    try {
      setCheck(await sendJson<ConnectionCheck>('POST', `${path(connection.id)}/test`, body))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!dirty || busy) return
    if (!window.confirm(`保存后 ${connection.name || connection.self_id} 会重新连接，这个 bot 停几秒钟。`)) return
    setBusy('save')
    setError('')
    try {
      await sendJson('PUT', path(connection.id), body)
      onSaved()
    } catch (e) {
      setError((e as Error).message)
      setBusy('')
    }
  }
  const tokenHint = (saved: boolean) => (saved ? '已设置；留空就不改' : '没有设置')

  return (
    <form onSubmit={(e) => void save(e)} className="feed-in mt-3">
      <div className="grid gap-4 rounded-xl border bg-muted/30 p-4 sm:grid-cols-2">
        <Field label="HTTP 地址" hint="发消息、查群用，例如 http://127.0.0.1:3010">
          {(control) => (
            <Input {...control} value={form.http_url} onChange={(e) => set('http_url')(e.target.value)} spellCheck={false} autoComplete="off" className="font-mono text-xs" />
          )}
        </Field>
        <Field label="WebSocket 地址" hint="收消息用，例如 ws://127.0.0.1:3011/">
          {(control) => (
            <Input {...control} value={form.ws_url} onChange={(e) => set('ws_url')(e.target.value)} spellCheck={false} autoComplete="off" className="font-mono text-xs" />
          )}
        </Field>
        <Field label="HTTP 令牌" hint={tokenHint(connection.http_token)}>
          {(control) => (
            <Input {...control} type="password" autoComplete="off" value={form.http_token} onChange={(e) => set('http_token')(e.target.value)} placeholder={connection.http_token ? '••••••••' : ''} />
          )}
        </Field>
        <Field label="WebSocket 令牌" hint={tokenHint(connection.ws_token)}>
          {(control) => (
            <Input {...control} type="password" autoComplete="off" value={form.ws_token} onChange={(e) => set('ws_token')(e.target.value)} placeholder={connection.ws_token ? '••••••••' : ''} />
          )}
        </Field>
        <p className="text-xs text-muted-foreground sm:col-span-2">
          地址和令牌在 SnowLuma 的管理页里能看到（每个号的 OneBot 设置）。地址换到另一台机器或另一个端口时，要把令牌重新填一遍：保存的令牌只发给原来的地址。
        </p>
        {check && (
          <div className="space-y-1 sm:col-span-2" role="status">
            <CheckLine label="HTTP" result={check.http} />
            <CheckLine label="WebSocket" result={check.ws} />
          </div>
        )}
        {error && (
          <p role="alert" className="text-xs text-destructive sm:col-span-2">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <Button type="submit" size="sm" disabled={!dirty || busy !== ''}>
            {busy === 'save' && <LoaderCircle className="size-3.5 animate-spin" />}
            保存并重新连接
          </Button>
          <Button type="button" size="sm" variant="outline" disabled={busy !== ''} onClick={() => void test()}>
            {busy === 'test' && <LoaderCircle className="size-3.5 animate-spin" />}
            测试连接
          </Button>
          <Button type="button" size="sm" variant="ghost" disabled={busy === 'save'} onClick={onClose}>
            取消
          </Button>
        </div>
      </div>
    </form>
  )
}

/** A platform other than QQ: its form, from the platform's own list of fields. A secret left empty stays as
 * saved. The account's id is fixed; a platform whose test would throw the bot offline has no test here. */
function PlatformEditor({
  connection,
  platform,
  onClose,
  onSaved,
}: {
  connection: QQConnection
  platform: PlatformView
  onClose: () => void
  onSaved: () => void
}) {
  const fields = platform.fields.filter((f) => !f.identity)
  const [form, setForm] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.name, f.secret ? '' : (connection.fields?.[f.name] ?? '')])),
  )
  const [busy, setBusy] = useState<'' | 'test' | 'save'>('')
  const [error, setError] = useState('')
  const [trial, setTrial] = useState<ConnectionTrial | null>(null)
  const set = (name: string, value: string) => {
    setForm((f) => ({ ...f, [name]: value }))
    setTrial(null)
  }
  const test = async () => {
    setBusy('test')
    setError('')
    setTrial(null)
    try {
      setTrial(await sendJson<ConnectionTrial>('POST', `${path(connection.id)}/test`, { fields: form }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (!window.confirm(`保存后 ${connection.name || connection.self_id} 会重新连接，这个 bot 停几秒钟。`)) return
    setBusy('save')
    setError('')
    try {
      await sendJson('PUT', path(connection.id), { fields: form })
      onSaved()
    } catch (e) {
      setError((e as Error).message)
      setBusy('')
    }
  }
  return (
    <form onSubmit={(e) => void save(e)} className="feed-in mt-3">
      <div className="grid gap-4 rounded-xl border bg-muted/30 p-4 sm:grid-cols-2">
        {fields.map((f) => (
          <Field key={f.name} label={f.label} hint={f.secret ? (connection.secrets?.[f.name] ? '已设置；留空就不改' : '没有设置') : f.hint}>
            {(control) => (
              <Input
                {...control}
                type={f.secret ? 'password' : 'text'}
                autoComplete="off"
                spellCheck={false}
                value={form[f.name]}
                placeholder={f.secret && connection.secrets?.[f.name] ? '••••••••' : ''}
                onChange={(e) => set(f.name, e.target.value)}
              />
            )}
          </Field>
        ))}
        {trial && (
          <div className="sm:col-span-2" role="status">
            <CheckLine label={platform.label} result={trial} />
          </div>
        )}
        {error && (
          <p role="alert" className="text-xs text-destructive sm:col-span-2">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <Button type="submit" size="sm" disabled={busy !== ''}>
            {busy === 'save' && <LoaderCircle className="size-3.5 animate-spin" />}
            保存并重新连接
          </Button>
          {!platform.probe_kicks && (
            <Button type="button" size="sm" variant="outline" disabled={busy !== ''} onClick={() => void test()}>
              {busy === 'test' && <LoaderCircle className="size-3.5 animate-spin" />}
              测试连接
            </Button>
          )}
          <Button type="button" size="sm" variant="ghost" disabled={busy === 'save'} onClick={onClose}>
            取消
          </Button>
        </div>
      </div>
    </form>
  )
}

/** 系统 page: how each account is reached, editable without the command line. QQ's OneBot addresses, and for the
 * other platforms what their own form holds; accounts are grouped by platform once there is more than one. */
export function AccountConnections() {
  const { accounts, systemVersion } = useLive()
  // systemVersion: another console saved a connection.
  const { data, error, missing, reload } = useLiveData<QQConnection[]>('/api/accounts/connections', [
    accounts.length,
    systemVersion,
  ])
  const { data: platforms } = usePlatforms()
  const [editing, setEditing] = useState<string | null>(null)
  const live = Object.fromEntries(accounts.map((a) => [a.id, a.status]))

  if (missing) return <p className="text-sm text-muted-foreground">正在运行的 nest 还不支持在这里改连接，重启 nest 后再看。</p>
  if (!data) return error ? <p role="alert" className="text-sm text-destructive">读取失败：{error}</p> : null
  if (data.length === 0) return <p className="text-sm text-muted-foreground">还没有账号。</p>

  const keyOf = (c: QQConnection) => c.platform ?? 'qq'
  const keys = [...new Set(data.map(keyOf))]
  const labelOf = (key: string) => platforms?.find((p) => p.key === key)?.label ?? key
  return (
    <div className="space-y-5">
      {keys.map((key) => (
        <div key={key}>
          {keys.length > 1 && <h3 className="mb-2 text-sm font-semibold">{labelOf(key)}</h3>}
          <ul className="divide-y">
            {data
              .filter((c) => keyOf(c) === key)
              .map((c) => {
                const status = live[c.id] ?? { state: c.state, detail: c.detail }
                const platform = platforms?.find((p) => p.key === key)
                return (
                  <li key={c.id} className="py-3 first:pt-0 last:pb-0">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                      <span className="flex items-center gap-2 text-sm font-medium">
                        <StatusDot tone={tone(status.state)} />
                        {c.name || c.self_id}
                        <span className="font-normal text-muted-foreground tabular-nums">{c.self_id}</span>
                      </span>
                      <span className="flex min-w-0 flex-1 flex-wrap gap-x-3 font-mono text-xs text-muted-foreground">
                        {key === 'qq' ? (
                          <>
                            <span className="truncate">{c.http_url}</span>
                            <span className="truncate">{c.ws_url}</span>
                          </>
                        ) : (
                          Object.values(c.fields ?? {})
                            .filter(Boolean)
                            .map((value) => (
                              <span key={value} className="truncate">
                                {value}
                              </span>
                            ))
                        )}
                      </span>
                      {editing !== c.id && (key === 'qq' || platform) && (
                        <Button size="sm" variant="outline" onClick={() => setEditing(c.id)}>
                          编辑
                        </Button>
                      )}
                    </div>
                    {status.state !== 'connected' && status.detail && editing !== c.id && (
                      <p className="mt-1 pl-[18px] text-xs text-muted-foreground">{status.detail}</p>
                    )}
                    {editing === c.id && key === 'qq' && (
                      <Editor
                        connection={c}
                        onClose={() => setEditing(null)}
                        onSaved={() => {
                          setEditing(null)
                          reload()
                        }}
                      />
                    )}
                    {editing === c.id && key !== 'qq' && platform && (
                      <PlatformEditor
                        connection={c}
                        platform={platform}
                        onClose={() => setEditing(null)}
                        onSaved={() => {
                          setEditing(null)
                          reload()
                        }}
                      />
                    )}
                  </li>
                )
              })}
          </ul>
        </div>
      ))}
    </div>
  )
}
