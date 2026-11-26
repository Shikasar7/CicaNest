import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, FolderOpen, Search } from 'lucide-react'

import { DesktopOptions } from '@/components/DesktopOptions'
import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { RemoteAccess } from '@/components/RemoteAccess'
import { BackupButton, StorageSettings } from '@/components/StorageSettings'
import { PathField, Section, ServiceRow, useRun, useSystem } from '@/components/system/parts'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { checkProxy } from '@/components/models/form'
import { getJson, sendJson, type LogLine, type SystemSettings, type SystemView } from '@/lib/api'
import { desktop } from '@/lib/desktop'
import { formatTime, formatUptime } from '@/lib/format'
import { useLive } from '@/lib/live'
import { cn } from '@/lib/utils'

/** nest itself: how long it has run, and restarting it from the desktop app. */
function NestRow({ system, reload }: { system: SystemView; reload: () => void }) {
  const nest = useRun(reload)
  const restartNest = () => {
    if (!desktop || !window.confirm('重启 nest？伙伴会停几秒钟，正在想的回复会被放弃。')) return
    void nest.run('restart', () => desktop!.restartNest())
  }
  return (
    <ul>
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
    </ul>
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


/** 系统: nest itself, the desktop app, remote access, logs, storage and the network. QQ and the other platforms
 * are on 平台接入, the look on 外观. */
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
        <Section title="nest">
          <NestRow system={data} reload={reload} />
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
      {data && typeof data.fetch_proxy === 'string' && (
        <Section title="网络">
          <div className="grid gap-5 md:grid-cols-2">
            <PathField
              label="外网代理"
              hint="监控这些要上外网的地方用它。例如 http://127.0.0.1:7890，留空表示不用代理"
              placeholder="http://127.0.0.1:7890"
              value={data.fetch_proxy}
              validate={checkProxy}
              onSave={(v) => saveSettings({ fetch_proxy: v.replace(/\/$/, '') })}
            />
          </div>
        </Section>
      )}
    </div>
  )
}
