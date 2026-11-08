import { useState, type FormEvent } from 'react'
import { Check, Copy, LoaderCircle } from 'lucide-react'

import { ResultText, type Result } from '@/components/Feedback'
import { Field } from '@/components/Field'
import { StatusDot } from '@/components/StatusDot'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { sendJson, type RemoteDevice, type RemoteView } from '@/lib/api'
import { describeAgent, formatAgo } from '@/lib/format'
import { useLive } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import { cn } from '@/lib/utils'

const KIND = { lan: '局域网', tailscale: 'Tailscale' } as const

/** Copy text; plain http on the home network has no Clipboard API, so fall back to a selected textarea. */
async function copy(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text)
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.append(area)
  area.select()
  const done = document.execCommand('copy')
  area.remove()
  if (!done) throw new Error('复制失败')
}

function Address({ url, label }: { url: string; label: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <li className="flex min-w-0 items-center gap-3 py-1.5">
      <span className="w-20 shrink-0 text-xs text-muted-foreground">{label}</span>
      <a href={url} className="min-w-0 truncate text-sm tabular-nums underline-offset-4 hover:underline">
        {url}
      </a>
      <button
        type="button"
        onClick={() =>
          void copy(url).then(() => {
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          })
        }
        aria-label={`复制 ${url}`}
        className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        {copied ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />}
      </button>
    </li>
  )
}

function PasswordForm({ hasPassword, onDone }: { hasPassword: boolean; onDone: (result: Result) => void }) {
  const [current, setCurrent] = useState('')
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // Judged once the second entry is as long as the first: no red while the user is still typing.
  const mismatch = again.length >= password.length && again.length > 0 && again !== password
  const ready = password.length >= 8 && again === password && (!hasPassword || current.length > 0)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!ready || busy) return
    setBusy(true)
    setError('')
    try {
      await sendJson('PUT', '/api/remote/password', { password, ...(hasPassword ? { current } : {}) })
      onDone({ ok: true, text: hasPassword ? '密码已修改，其他设备需要用新密码重新登录。' : '密码已设置。' })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="feed-in">
      <div className="grid max-w-xl gap-4 sm:grid-cols-2">
        <input type="text" name="username" autoComplete="username" value="nest" readOnly hidden />
        {hasPassword && (
          <Field label="现在的密码" className="sm:col-span-2 sm:max-w-[calc(50%-0.5rem)]">
            {(control) => (
              <Input
                {...control}
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
              />
            )}
          </Field>
        )}
        <Field label="新密码" hint="至少 8 个字符">
          {(control) => (
            <Input
              {...control}
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>
        <Field label="再输一遍" error={mismatch ? '两次输入的不一样' : undefined}>
          {(control) => (
            <Input
              {...control}
              type="password"
              autoComplete="new-password"
              value={again}
              onChange={(e) => setAgain(e.target.value)}
            />
          )}
        </Field>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button type="submit" size="sm" disabled={!ready || busy}>
          {busy && <LoaderCircle className="size-3.5 animate-spin" />}
          {hasPassword ? '修改密码' : '设置密码'}
        </Button>
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
      </div>
    </form>
  )
}

function DeviceRow({ device, busy, onSignOut }: { device: RemoteDevice; busy: boolean; onSignOut: () => void }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
      <span className="text-sm font-medium">{describeAgent(device.agent)}</span>
      {device.current && (
        <span className="rounded-full bg-ok/12 px-2 py-0.5 text-[11px] font-medium text-ok">这台设备</span>
      )}
      <span className="text-xs text-muted-foreground tabular-nums">
        {device.ip}，{device.current ? '正在使用' : `${formatAgo(device.last_seen_at)}用过`}
      </span>
      <Button size="sm" variant="ghost" className="ml-auto" disabled={busy} onClick={onSignOut}>
        {device.current ? '退出登录' : '让它退出'}
      </Button>
    </li>
  )
}

/** 系统 page: whether phones and other computers can open the console, how, and who is signed in. */
export function RemoteAccess() {
  const { systemVersion } = useLive()
  const { data, error, missing, reload } = useLiveData<RemoteView>('/api/remote', [systemVersion])
  const [busy, setBusy] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [changing, setChanging] = useState(false)

  if (missing) return <p className="text-sm text-muted-foreground">正在运行的 nest 还不支持远程访问，重启 nest 后再看。</p>
  if (!data) return error ? <p role="alert" className="text-sm text-destructive">读取失败：{error}</p> : null

  const act = async (name: string, action: () => Promise<unknown>, done?: Result) => {
    setBusy(name)
    setResult(null)
    try {
      await action()
      if (done) setResult(done)
      reload()
    } catch (e) {
      setResult({ ok: false, text: (e as Error).message })
    } finally {
      setBusy('')
    }
  }
  const switchTo = (enabled: boolean) =>
    act('switch', () => sendJson('PATCH', '/api/remote', { enabled }), {
      ok: true,
      text: enabled === data.enabled ? '已恢复。' : `已保存，重启 nest 后${enabled ? '开启' : '关闭'}。`,
    })
  const signOut = (device: RemoteDevice) =>
    act(device.id, async () => {
      if (device.current) {
        await sendJson('POST', '/api/logout')
        window.location.reload()
      } else {
        await sendJson('DELETE', `/api/remote/devices/${encodeURIComponent(device.id)}`)
      }
    })
  const pending = data.configured !== data.enabled
  const others = data.devices.filter((d) => !d.current)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 space-y-1">
          <p className="flex items-center gap-2 text-sm font-medium">
            <StatusDot tone={data.enabled ? 'ok' : 'off'} />
            {data.enabled ? '已开启' : '已关闭'}
          </p>
          <p className="text-xs text-muted-foreground">
            {data.enabled
              ? '家里局域网和 Tailscale 里的手机、电脑可以用密码打开控制台；外网的访问一律拒绝。'
              : '只有这台电脑能打开控制台。'}
          </p>
          {pending && (
            <p className="text-xs text-warn">重启 nest 后{data.configured ? '开启' : '关闭'}远程访问。</p>
          )}
        </div>
        <label className="flex items-center gap-2.5 text-sm">
          <Switch
            checked={data.configured}
            disabled={busy === 'switch' || (!data.password_set && !data.configured)}
            onCheckedChange={(v) => void switchTo(v)}
          />
          允许其他设备
        </label>
      </div>
      {!data.password_set && !data.configured && (
        <p className="-mt-3 text-xs text-muted-foreground">先在下面设置一个登录密码，才能开启。</p>
      )}

      {data.enabled && (data.addresses.length > 0 || data.names.length > 0) && (
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">在其他设备的浏览器里打开</h3>
          <ul className="divide-y">
            {data.addresses.map((a) => (
              <Address key={a.url} url={a.url} label={KIND[a.kind]} />
            ))}
            {data.names.map((n) => (
              <Address key={n.url} url={n.url} label={n.name.endsWith('.ts.net') ? 'Tailscale' : '名字'} />
            ))}
          </ul>
        </div>
      )}

      <div>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <h3 className="text-xs font-medium text-muted-foreground">登录密码</h3>
          {data.password_set && !changing && (
            <>
              <span className="text-xs">已设置</span>
              <Button size="sm" variant="outline" onClick={() => setChanging(true)}>
                修改
              </Button>
            </>
          )}
        </div>
        {(!data.password_set || changing) && (
          <PasswordForm
            hasPassword={data.password_set}
            onDone={(done) => {
              setChanging(false)
              setResult(done)
              reload()
            }}
          />
        )}
      </div>

      {data.devices.length > 0 && (
        <div>
          <div className="mb-1 flex flex-wrap items-center gap-3">
            <h3 className="text-xs font-medium text-muted-foreground">已登录的设备</h3>
            {others.length > 1 && (
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto"
                disabled={busy !== ''}
                onClick={() => void act('others', () => sendJson('DELETE', '/api/remote/devices'))}
              >
                让其他设备都退出
              </Button>
            )}
          </div>
          <ul className={cn('divide-y', busy === 'others' && 'opacity-60')}>
            {data.devices.map((device) => (
              <DeviceRow
                key={device.id}
                device={device}
                busy={busy !== ''}
                onSignOut={() => void signOut(device)}
              />
            ))}
          </ul>
        </div>
      )}
      <ResultText result={result} />
    </div>
  )
}
