import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ArrowDown, FolderOpen, Search } from 'lucide-react'

import { AccountConnections } from '@/components/AccountConnections'
import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { QRLogin } from '@/components/QRLogin'
import { RemoteAccess } from '@/components/RemoteAccess'
import { BackupButton, StorageSettings } from '@/components/StorageSettings'
import { StatusDot, type Tone } from '@/components/StatusDot'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Segmented } from '@/components/Segmented'
import { checkProxy } from '@/components/models/form'
import { ApiError, getJson, sendJson, type AuthInfo, type LogLine, type QQContainer, type SystemSettings, type SystemView } from '@/lib/api'
import { desktop } from '@/lib/desktop'
import { formatTime, formatUptime } from '@/lib/format'
import { useLive } from '@/lib/live'
import { RECIPIENT_HINT } from '@/lib/platforms'
import { cn } from '@/lib/utils'

function useSystem() {
  const { systemVersion, accounts } = useLive()
  const [data, setData] = useState<SystemView | null>(null)
  const [error, setError] = useState('')
  const [missing, setMissing] = useState(false)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    let cancelled = false
    getJson<SystemView>('/api/system').then(
      (value) => {
        if (cancelled) return
        setData(value)
        setError('')
      },
      (e: Error) => {
        if (cancelled) return
        setMissing(e instanceof ApiError && e.status === 404)
        setError(e.message)
      },
    )
    return () => {
      cancelled = true
    }
    // Account status changes (QQ coming online) also change what this page shows.
  }, [systemVersion, accounts, nonce])
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, missing, reload }
}

/** One action at a time per service row, with its result or error shown right there. */
function useRun(reload: () => void) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const run = async (name: string, action: () => Promise<unknown>) => {
    setBusy(name)
    setError('')
    try {
      await action()
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  return { busy, error, run }
}

function ServiceRow({
  name,
  tone,
  state,
  detail,
  error,
  children,
  below,
}: {
  name: string
  tone: Tone
  state: string
  detail?: ReactNode
  error?: string
  children?: ReactNode
  /** Shown under the row, full width. */
  below?: ReactNode
}) {
  return (
    <li className="flex flex-wrap items-start gap-x-6 gap-y-3 py-4 first:pt-0 last:pb-0">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2.5">
          <StatusDot tone={tone} />
          <span className="font-medium">{name}</span>
          <span className="text-sm text-muted-foreground">{state}</span>
        </div>
        {detail && <div className="pl-[18px] text-xs leading-relaxed text-muted-foreground">{detail}</div>}
        {error && (
          <p role="alert" className="pl-[18px] text-xs text-destructive">
            {error}
          </p>
        )}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
      {below && <div className="basis-full pl-[18px]">{below}</div>}
    </li>
  )
}

/** Whether this browser runs on nest's own machine (addresses like 127.0.0.1 only work there). */
function useOnThisMachine(): boolean {
  const [local, setLocal] = useState(true)
  useEffect(() => {
    getJson<AuthInfo>('/api/auth').then(
      (info) => setLocal(info.local),
      () => setLocal(true), // a nest from before remote access: always this machine
    )
  }, [])
  return local
}

function QQLogin({ url, onThisMachine }: { url: string; onThisMachine: boolean }) {
  if (onThisMachine) {
    return (
      <Button size="sm" variant="outline" asChild>
        <a href={url} target="_blank" rel="noreferrer">
          扫码登录
        </a>
      </Button>
    )
  }
  const port = (() => {
    try {
      return new URL(url).port || '80'
    } catch {
      return '6081'
    }
  })()
  return (
    <p className="max-w-xs text-xs text-muted-foreground">
      扫码页只在 nest 那台电脑上能打开。在这台设备上：先运行{' '}
      <code className="rounded bg-muted px-1 py-0.5 text-[11px]">ssh -L {port}:127.0.0.1:{port} 那台电脑</code>，再打开 {url}
    </p>
  )
}

/** A NapCat account's 回退密码: typed here, only its MD5 kept on the Mac. Saving makes the container again, so
 * the account drops for a few seconds. */
function PasswordForm({ box, who, onDone }: { box: QQContainer; who: string; onDone: () => void }) {
  const id = useId()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState<'' | 'save' | 'clear'>('')
  const [error, setError] = useState('')
  const send = async (value: string, kind: 'save' | 'clear') => {
    if (kind === 'clear' && !window.confirm(`清除 ${who || box.name} 的回退密码？容器会重建，这个号断开十几秒。`)) return
    setBusy(kind)
    setError('')
    try {
      await sendJson('PUT', '/api/system/qq/password', { container: box.name, password: value })
      setPassword('')
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  return (
    <form
      className="space-y-2 rounded-xl border bg-muted/40 p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (password) void send(password, 'save')
      }}
    >
      <Label htmlFor={id}>{who || box.self_id} 的 QQ 密码</Label>
      <div className="flex flex-wrap gap-2">
        <Input
          id={id}
          type="password"
          autoComplete="new-password"
          maxLength={64}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-64"
        />
        <Button type="submit" size="sm" disabled={!!busy || !password}>
          {busy === 'save' ? '正在重建容器…' : '保存'}
        </Button>
        {box.password_set && (
          <Button type="button" size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10 hover:text-destructive" disabled={!!busy} onClick={() => void send('', 'clear')}>
            {busy === 'clear' ? '正在重建容器…' : '清除'}
          </Button>
        )}
        <Button type="button" size="sm" variant="ghost" disabled={!!busy} onClick={onDone}>
          收起
        </Button>
      </div>
      <p className={cn('text-xs', error ? 'text-destructive' : 'text-muted-foreground')} role={error ? 'alert' : undefined}>
        {error ||
          `${box.password_set ? '已经设了回退密码，再保存会换成新的。' : ''}保存的登录失效时，QQ 先用这个密码登录，不行再出二维码。Mac 上只存密码的 MD5，不存密码本身，但拿到它也能登录这个号。账号被冻结、要人脸验证时它帮不上忙。保存或清除都会重建容器，这个号断开十几秒，登录状态不丢。`}
      </p>
    </form>
  )
}

/** One QQ container (docker mode): its state, start / restart / stop, its login QR code (NapCat, shown
 * here) or the page to scan it on, and its 回退密码 (NapCat). */
function ContainerRow({
  box,
  who,
  reload,
  onThisMachine,
}: {
  box: QQContainer
  who: string
  reload: () => void
  onThisMachine: boolean
}) {
  const run = useRun(reload)
  const [scanning, setScanning] = useState(false)
  const [password, setPassword] = useState(false)
  const passwordable = !!box.qr_login && !!box.self_id && box.password_set !== undefined
  const act = (action: 'start' | 'stop' | 'restart', question?: string) => {
    if (question && !window.confirm(question)) return
    void run.run(action, () => sendJson('POST', `/api/system/snowluma/${action}?container=${encodeURIComponent(box.name)}`))
  }
  // A stopped NapCat container (nest stops one that waits for a scan nobody makes): start it and show its code.
  const startAndScan = () => {
    setScanning(true)
    act('start')
  }
  const state = box.problem ? '连不上 Docker' : !box.found ? '容器还没建' : box.running ? '运行中' : '没有运行'
  const detail = box.problem ? (
    box.problem
  ) : !box.found ? (
    <>Docker 里还没有叫 {box.name} 的容器：在 nest 的 deploy/mac 文件夹里运行 docker compose up -d，见 deploy/mac/README.md。</>
  ) : box.qr_login ? (
    `把这个容器里的 QQ 接到 nest。号没上线时点「扫码登录」，用手机 QQ 扫码。登录失效后没人扫码时，nest 会停下这个容器，不让它一直刷新二维码。${box.password_set ? '已设回退密码。' : ''}`
  ) : (
    '把这个容器里的 QQ 接到 nest。号没上线时点「扫码登录」，用手机 QQ 扫码。'
  )
  return (
    <ServiceRow
      name={who ? `${box.name}（${who}）` : box.name}
      tone={box.problem ? 'bad' : !box.found ? 'off' : box.running ? 'ok' : 'bad'}
      state={state}
      detail={detail}
      error={run.error}
      below={
        password ? (
          <PasswordForm
            box={box}
            who={who}
            onDone={() => {
              setPassword(false)
              reload()
            }}
          />
        ) : scanning && box.running ? (
          <QRLogin container={box.name} onClose={() => setScanning(false)} />
        ) : undefined
      }
    >
      {box.found && !box.running && (
        <>
          {box.qr_login && (
            <Button size="sm" disabled={!!run.busy} onClick={startAndScan}>
              {run.busy === 'start' && scanning ? '启动中…' : '扫码登录'}
            </Button>
          )}
          <Button size="sm" variant={box.qr_login ? 'outline' : 'default'} disabled={!!run.busy} onClick={() => act('start')}>
            {run.busy === 'start' && !scanning ? '启动中…' : '启动'}
          </Button>
        </>
      )}
      {box.running && (
        <>
          {box.qr_login ? (
            !scanning && (
              <Button size="sm" variant="outline" onClick={() => setScanning(true)}>
                扫码登录
              </Button>
            )
          ) : (
            box.login_url && <QQLogin url={box.login_url} onThisMachine={onThisMachine} />
          )}
          <Button size="sm" variant="outline" disabled={!!run.busy} onClick={() => act('restart', `重启容器 ${box.name}？里面的 QQ 会重启，这个号要等它重新上线，一般不用重新扫码。`)}>
            {run.busy === 'restart' ? '重启中…' : '重启'}
          </Button>
          <Button size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10 hover:text-destructive" disabled={!!run.busy} onClick={() => act('stop', `停止容器 ${box.name}？这个号会断开，直到再次启动。登录状态会保留。`)}>
            {run.busy === 'stop' ? '停止中…' : '停止'}
          </Button>
        </>
      )}
      {passwordable && !password && (
        <Button size="sm" variant="outline" disabled={!!run.busy} onClick={() => setPassword(true)}>
          回退密码
        </Button>
      )}
    </ServiceRow>
  )
}

function Services({ system, reload }: { system: SystemView; reload: () => void }) {
  const nest = useRun(reload)
  const snow = useRun(reload)
  const qq = useRun(reload)
  const onThisMachine = useOnThisMachine()
  const online = system.accounts.filter((a) => a.state === 'connected').length
  const sl = system.snowluma
  const inDocker = sl.mode === 'docker'
  // Docker mode on a nest that lists its containers: a row each, with its own login page.
  const boxes = inDocker ? system.containers : undefined

  const stopSnow = () => {
    const what = inDocker
      ? `停止 SnowLuma 容器（${sl.container}）？里面的 QQ 也会一起停下，bot 全部断开，直到再次启动。登录状态会保留。`
      : '停止 SnowLuma？三个 bot 和主号的连接都会断开，直到再次启动。QQ 本身不会退出。'
    if (!window.confirm(what)) return
    void snow.run('stop', () => sendJson('POST', '/api/system/snowluma/stop'))
  }
  const restartSnow = () => {
    const what = inDocker
      ? `重启 SnowLuma 容器（${sl.container}）？里面的 QQ 也会重启，bot 要等它们重新上线，一般不用重新扫码。`
      : '重启 SnowLuma？bot 会断开几秒钟，之后 nest 会自动重新连上。'
    if (!window.confirm(what)) return
    void snow.run('restart', () => sendJson('POST', '/api/system/snowluma/restart'))
  }
  const snowState = sl.problem
    ? '连不上 Docker'
    : !sl.configured
      ? inDocker
        ? '容器还没建'
        : '还没设置位置'
      : sl.running
        ? '运行中'
        : '没有运行'
  const snowDetail = sl.problem ? (
    sl.problem
  ) : !sl.configured ? (
    inDocker ? (
      <>
        Docker 里还没有叫 {sl.container} 的容器：在 nest 的 deploy/mac 文件夹里运行 docker compose up -d，见 deploy/mac/README.md。
      </>
    ) : (
      '在下面的「位置和网络」里填 SnowLuma 文件夹后才能从这里启动。'
    )
  ) : (
    <>
      负责把 QQ 接到 nest{inDocker ? `，在 Docker 容器 ${sl.container} 里运行` : ''}。bot 号没上线时，可以打开{' '}
      <a href={sl.webui_url} target="_blank" rel="noreferrer" className="underline underline-offset-4 hover:text-foreground">
        SnowLuma 管理页
      </a>{' '}
      查看{inDocker && !onThisMachine ? '（只在 nest 那台电脑上能打开）' : ''}。
    </>
  )
  const restartNest = () => {
    if (!desktop || !window.confirm('重启 nest？bot 会停几秒钟，正在想的回复会被放弃。')) return
    void nest.run('restart', () => desktop!.restartNest())
  }

  return (
    <ul className="divide-y">
      <ServiceRow
        name="nest"
        tone="ok"
        state={`已运行 ${formatUptime(system.nest.uptime_seconds)}`}
        detail={`版本 ${system.nest.version}，占用内存 ${Math.round(system.nest.memory_mb)} MB`}
        error={nest.error}
      >
        {desktop && (
          <Button size="sm" variant="outline" disabled={!!nest.busy} onClick={restartNest}>
            {nest.busy ? '重启中…' : '重启 nest'}
          </Button>
        )}
      </ServiceRow>
      {boxes?.map((box) => (
        <ContainerRow
          key={box.name}
          box={box}
          who={system.accounts.find((a) => box.self_id && a.self_id === box.self_id)?.name ?? ''}
          reload={reload}
          onThisMachine={onThisMachine}
        />
      ))}
      {!boxes && (
        <ServiceRow
          name="SnowLuma"
          tone={sl.problem ? 'bad' : !sl.configured ? 'off' : sl.running ? 'ok' : 'bad'}
          state={snowState}
          detail={snowDetail}
          error={snow.error}
        >
          {sl.configured && !sl.running && (
            <Button size="sm" disabled={!!snow.busy} onClick={() => void snow.run('start', () => sendJson('POST', '/api/system/snowluma/start'))}>
              {snow.busy === 'start' ? '启动中…' : '启动'}
            </Button>
          )}
          {sl.running && (
            <>
              <Button size="sm" variant="outline" disabled={!!snow.busy} onClick={restartSnow}>
                {snow.busy === 'restart' ? '重启中…' : '重启'}
              </Button>
              <Button size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10 hover:text-destructive" disabled={!!snow.busy} onClick={stopSnow}>
                {snow.busy === 'stop' ? '停止中…' : '停止'}
              </Button>
            </>
          )}
        </ServiceRow>
      )}
      <ServiceRow
        name="QQ"
        tone={system.accounts.length === 0 ? 'off' : online === system.accounts.length ? 'ok' : online === 0 ? 'bad' : 'wait'}
        state={`${online} / ${system.accounts.length} 个号在线`}
        detail={
          <>
            {inDocker
              ? 'QQ 在 Docker 容器里运行。'
              : system.qq.clients
                ? `正在运行 ${system.qq.clients} 个 QQ 进程。`
                : '没有找到正在运行的 QQ。'}
            {online < system.accounts.length &&
              (inDocker ? ' 没上线的号点「扫码登录」，用手机 QQ 扫码。' : ' 没上线的号需要在 QQ 里登录，SnowLuma 会自动接上。')}
            <span className="mt-1 flex flex-wrap gap-x-3">
              {system.accounts.map((a) => (
                <span key={a.id} className="inline-flex items-center gap-1">
                  <StatusDot tone={a.state === 'connected' ? 'ok' : a.state === 'connecting' || a.state === 'reconnecting' ? 'wait' : 'bad'} className="size-1.5" />
                  {a.name || a.self_id}
                  {a.state === 'deaf' && <span className="text-destructive">收不到消息</span>}
                </span>
              ))}
            </span>
          </>
        }
        error={qq.error}
      >
        {inDocker && !boxes && system.qq.login_url && <QQLogin url={system.qq.login_url} onThisMachine={onThisMachine} />}
        {!inDocker && system.qq.configured && (
          <Button size="sm" variant="outline" disabled={!!qq.busy} onClick={() => void qq.run('open', () => sendJson('POST', '/api/system/qq/open'))}>
            {qq.busy ? '打开中…' : '打开 QQ'}
          </Button>
        )}
      </ServiceRow>
    </ul>
  )
}

function PathField({
  label,
  hint,
  value,
  placeholder,
  validate,
  onSave,
}: {
  label: string
  hint: string
  value: string
  placeholder?: string
  /** Why the trimmed value cannot be saved, checked before sending; undefined = fine. */
  validate?: (next: string) => string | undefined
  onSave: (next: string) => Promise<unknown>
}) {
  const id = useId()
  const [draft, setDraft] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const current = draft ?? value
  const save = async () => {
    if (draft === null || draft.trim() === value) {
      setDraft(null)
      return
    }
    const problem = validate?.(draft.trim())
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError('')
    try {
      await onSave(draft.trim())
      setDraft(null)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex gap-2">
        <Input
          id={id}
          value={current}
          placeholder={placeholder}
          spellCheck={false}
          autoComplete="off"
          aria-invalid={error ? true : undefined}
          aria-describedby={`${id}-hint`}
          onChange={(e) => {
            setDraft(e.target.value)
            setError('')
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) void save()
            if (e.key === 'Escape') {
              setDraft(null)
              setError('')
            }
          }}
          className="font-mono text-xs"
        />
        <Button size="sm" variant="outline" disabled={busy || draft === null} onClick={() => void save()} className="shrink-0">
          {busy ? '保存中…' : '保存'}
        </Button>
      </div>
      <p id={`${id}-hint`} className={cn('text-xs', error ? 'text-destructive' : 'text-muted-foreground')} role={error ? 'alert' : undefined}>
        {error || (saved ? '已保存' : hint)}
      </p>
    </div>
  )
}

const LEVELS = [
  { value: 'INFO', label: '全部' },
  { value: 'WARNING', label: '警告和错误' },
  { value: 'ERROR', label: '只看错误' },
] as const
const RANK: Record<LogLine['level'], number> = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 }
const MAX_LINES = 1000

function Logs() {
  const { subscribe } = useLive()
  const [lines, setLines] = useState<LogLine[] | null>(null)
  const [error, setError] = useState('')
  const [level, setLevel] = useState<(typeof LEVELS)[number]['value']>('INFO')
  const [query, setQuery] = useState('')
  // null while following the newest line; otherwise the last line that was on screen when the reader scrolled up.
  const [seenUpTo, setSeenUpTo] = useState<number | null>(null)
  // Lines up to this id came with the first load and do not animate in.
  const [loadedUpTo, setLoadedUpTo] = useState(0)
  const box = useRef<HTMLDivElement>(null)
  const searchId = useId()

  useEffect(() => {
    getJson<LogLine[]>(`/api/logs?limit=${MAX_LINES}&level=INFO`).then(
      (value) => {
        const last = value.at(-1)?.id ?? 0
        setLoadedUpTo(last)
        setLines((live) => [...value, ...(live ?? []).filter((l) => l.id > last)].slice(-MAX_LINES))
      },
      (e: Error) => setError(e.message),
    )
    return subscribe((item) => {
      if (item.kind !== 'log') return
      setLines((current) => [...(current ?? []), item.line].slice(-MAX_LINES))
    })
  }, [subscribe])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (lines ?? []).filter(
      (l) => RANK[l.level] >= RANK[level] && (!q || l.message.toLowerCase().includes(q) || l.logger.toLowerCase().includes(q)),
    )
  }, [lines, level, query])

  const lastId = shown.at(-1)?.id ?? 0
  const unseen = seenUpTo === null ? 0 : shown.filter((l) => l.id > seenUpTo).length
  // Stay at the newest line while following.
  useLayoutEffect(() => {
    const el = box.current
    if (el && seenUpTo === null) el.scrollTop = el.scrollHeight
  }, [lastId, seenUpTo])

  const onScroll = () => {
    const el = box.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    setSeenUpTo((current) => (atBottom ? null : (current ?? lastId)))
  }
  const jump = () => {
    box.current?.scrollTo({ top: box.current.scrollHeight, behavior: 'smooth' })
    setSeenUpTo(null)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div role="radiogroup" aria-label="显示哪些日志" className="inline-flex rounded-lg bg-muted p-0.5">
          {LEVELS.map((l) => (
            <button
              key={l.value}
              type="button"
              role="radio"
              aria-checked={level === l.value}
              onClick={() => setLevel(l.value)}
              className={cn(
                'rounded-md px-3 py-1 text-xs transition-all duration-200',
                level === l.value ? 'bg-card font-medium text-foreground shadow-[var(--shadow-lift)]' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {l.label}
            </button>
          ))}
        </div>
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <label htmlFor={searchId} className="sr-only">
            搜索日志
          </label>
          <Input id={searchId} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索，例如 阿鲸 或 失败" className="h-8 pl-8 text-xs" />
        </div>
        {desktop && (
          <Button size="sm" variant="ghost" onClick={() => void desktop!.openLogsFolder()}>
            <FolderOpen />
            打开日志文件夹
          </Button>
        )}
      </div>
      <div className="relative">
        <div
          ref={box}
          onScroll={onScroll}
          role="log"
          aria-live="off"
          aria-label="nest 日志"
          className="h-96 overflow-y-auto rounded-xl border bg-muted/60 px-1 py-2 font-mono text-[12px] leading-[1.65]"
        >
          {lines === null ? (
            <p className="px-3 py-2 text-muted-foreground">{error ? `读取日志失败：${error}` : '读取中…'}</p>
          ) : shown.length === 0 ? (
            <p className="px-3 py-2 text-muted-foreground">{query ? '没有匹配的日志。' : '这一级别还没有日志。'}</p>
          ) : (
            shown.map((l) => (
              <div
                key={l.id}
                className={cn(
                  'flex gap-3 rounded-md px-2.5 py-0.5 hover:bg-card/70',
                  l.id > loadedUpTo && 'animate-in fade-in-0 duration-300',
                  RANK[l.level] >= RANK.ERROR && 'bg-destructive/5',
                )}
              >
                <span className="shrink-0 text-muted-foreground tabular-nums">{formatTime(l.at).slice(-8)}</span>
                <span
                  className={cn(
                    'w-14 shrink-0',
                    RANK[l.level] >= RANK.ERROR ? 'text-destructive' : l.level === 'WARNING' ? 'text-warn' : 'text-muted-foreground',
                  )}
                >
                  {l.level === 'INFO' ? '信息' : l.level === 'WARNING' ? '警告' : l.level === 'DEBUG' ? '调试' : '错误'}
                </span>
                <span className="min-w-0 flex-1 break-words whitespace-pre-wrap">{l.message}</span>
              </div>
            ))
          )}
        </div>
        {unseen > 0 && (
          <button
            type="button"
            onClick={jump}
            className="animate-in fade-in-0 slide-in-from-bottom-2 absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-primary px-3 py-1.5 text-xs text-primary-foreground shadow-[var(--shadow-lift)] transition-transform active:scale-95"
          >
            <ArrowDown className="size-3.5" />
            {unseen} 条新日志
          </button>
        )}
      </div>
    </div>
  )
}

function DesktopOptions() {
  const [autostart, setAutostart] = useState<boolean | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    desktop?.getAutostart().then(setAutostart, () => setAutostart(null))
  }, [])
  if (!desktop) return null
  const toggle = async (next: boolean) => {
    setError('')
    setAutostart(next)
    try {
      await desktop!.setAutostart(next)
    } catch (e) {
      setAutostart(!next)
      setError((e as Error).message)
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Switch checked={autostart ?? false} disabled={autostart === null} onCheckedChange={(v) => void toggle(v)} aria-label="开机时自动启动 nest" />
      <span className="text-sm">开机时自动启动 nest</span>
      <span className="text-xs text-muted-foreground">登录 Windows 后在后台打开，右下角会出现 nest 图标</span>
      {error && <p role="alert" className="w-full text-xs text-destructive">{error}</p>}
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border bg-card p-5">
      <h2 className="mb-4 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  )
}

export function System() {
  const { data, error, missing, reload } = useSystem()
  const saveSettings = (body: SystemSettings) => sendJson('PATCH', '/api/system/settings', body).then(reload)
  return (
    <div className="flex flex-col gap-4">
      {missing ? (
        <p className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
          正在运行的 nest 版本还不支持这一页。重启 nest 后再打开。
        </p>
      ) : data === null ? (
        error ? <FirstLoad error={error} what="系统状态" /> : null
      ) : (
        <StaleNotice error={error} />
      )}
      {data && (
        <Section title="服务">
          <Services system={data} reload={reload} />
        </Section>
      )}
      {data?.offline_alert && (
        <Section title="掉线提醒">
          <div className="grid gap-5 md:grid-cols-2">
            <PathField
              label="私聊通知谁"
              hint={`某个 bot 掉线够久时，由同平台还在线的 bot 私聊这个号一次，重新连上时再说一声。留空表示不提醒。${RECIPIENT_HINT}`}
              placeholder="你自己的 QQ 号"
              value={data.offline_alert.to}
              validate={(v) => (v && !v.includes(':') && !/^[1-9]\d{4,11}$/.test(v) ? '要填一个 QQ 号（5–12 位数字），留空表示不提醒' : undefined)}
              onSave={(v) => saveSettings({ offline_alert_to: v })}
            />
            <PathField
              label="掉线多少分钟后通知"
              hint="1–120 分钟。短暂掉线（每周的自动重启、QQ 自己重登）不会提醒"
              value={String(data.offline_alert.minutes)}
              validate={(v) => (/^\d+$/.test(v) && +v >= 1 && +v <= 120 ? undefined : '填 1 到 120 之间的整数')}
              onSave={(v) => saveSettings({ offline_alert_minutes: Number(v) })}
            />
          </div>
        </Section>
      )}
      {desktop && (
        <Section title="桌面应用">
          <DesktopOptions />
        </Section>
      )}
      <Section title="远程访问">
        <RemoteAccess />
      </Section>
      <Section title="日志">
        <Logs />
      </Section>
      {data && (
        <Section title="存储">
          <StorageSettings />
          <div className="mt-5">
            <BackupButton />
          </div>
        </Section>
      )}
      {data && (
        <Section title="账号连接">
          <AccountConnections />
        </Section>
      )}
      {data && (
        <Section title="位置和网络">
          {data.snowluma.mode && (
            <div className="mb-5 space-y-1.5">
              <p id="snowluma-mode" className="text-sm font-medium">
                SnowLuma 的运行方式
              </p>
              <Segmented
                labelledBy="snowluma-mode"
                options={[
                  { value: 'program', label: '这台电脑上的程序' },
                  { value: 'docker', label: 'Docker 容器' },
                ]}
                value={data.snowluma.mode}
                onChange={(mode) => void saveSettings({ snowluma_mode: mode })}
              />
              <p className="text-xs text-muted-foreground">
                {data.snowluma.mode === 'docker'
                  ? 'SnowLuma 和 QQ 在一个 Docker 容器里：nest 通过 docker 命令启动、停止它，QQ 用扫码页登录。'
                  : 'SnowLuma 是这台电脑上的一个文件夹，QQ 是这台电脑上登录的 QQ（Windows 的做法）。'}
              </p>
            </div>
          )}
          <div className="grid gap-5 md:grid-cols-2">
            {data.snowluma.mode === 'docker' ? (
              <>
                <PathField
                  label="QQ 容器"
                  hint="docker compose 里 container_name 的值，几个容器用逗号隔开"
                  placeholder="napcat-bot1, napcat-bot2, napcat-bot3"
                  value={data.snowluma.container ?? 'snowluma'}
                  onSave={(v) => saveSettings({ snowluma_container: v })}
                />
                <PathField
                  label="扫码页地址"
                  hint="Docker 没说容器的扫码页在哪时用这个（SnowLuma 的远程桌面 noVNC）"
                  placeholder="http://127.0.0.1:6081"
                  value={data.qq.login_url ?? ''}
                  onSave={(v) => saveSettings({ qq_login_url: v })}
                />
              </>
            ) : (
              <>
                <PathField
                  label="SnowLuma 文件夹"
                  hint="里面要有 node.exe 和 index.mjs"
                  value={data.snowluma.dir}
                  onSave={(v) => saveSettings({ snowluma_dir: v })}
                />
                <PathField
                  label="QQ 程序"
                  hint="QQ.exe 的完整路径，用来从这里打开 QQ"
                  value={data.qq.executable}
                  onSave={(v) => saveSettings({ qq_executable: v })}
                />
              </>
            )}
            {typeof data.fetch_proxy === 'string' && (
              <PathField
                label="外网代理"
                hint="例如 http://127.0.0.1:7890，留空表示不用代理"
                placeholder="http://127.0.0.1:7890"
                value={data.fetch_proxy}
                validate={checkProxy}
                onSave={(v) => saveSettings({ fetch_proxy: v.replace(/\/$/, '') })}
              />
            )}
          </div>
        </Section>
      )}
    </div>
  )
}
