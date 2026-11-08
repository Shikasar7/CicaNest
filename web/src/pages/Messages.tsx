import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { DecisionCard } from '@/components/DecisionCard'
import { Badge } from '@/components/ui/badge'
import { getJson, type Decision, type Group, type Message, type Timeline } from '@/lib/api'
import { formatAgo, formatTime } from '@/lib/format'
import { useBots, useLive } from '@/lib/live'
import type { Bot } from '@/lib/api'
import { cn } from '@/lib/utils'

function groupTitle(group: Group): string {
  if (group.kind === 'group') return group.name || `群 ${group.external_id}`
  // A private chat belongs to one of our accounts: the same person may talk to each bot.
  const bot = group.channels[0]?.account_name
  return `${bot ? `${bot}的` : ''}私聊：${group.name || group.external_id}`
}

function GroupList({ groups, selected, onSelect }: {
  groups: Group[]
  selected: string | null
  onSelect: (key: string) => void
}) {
  if (groups.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">还没有收到消息。账号在线后，群里有人说话就会出现在这里。</p>
  }
  return (
    <ul className="divide-y">
      {groups.map((group) => (
        <li key={group.key}>
          <button
            type="button"
            onClick={() => onSelect(group.key)}
            className={cn('w-full px-4 py-3 text-left text-sm hover:bg-muted/60', selected === group.key && 'bg-muted')}
          >
            <div className="truncate font-medium">{groupTitle(group)}</div>
            <div className="flex justify-between gap-2 text-xs text-muted-foreground">
              <span className="truncate">{group.channels.map((c) => c.account_name).join('、')}</span>
              <span className="shrink-0">{group.last_message_at ? formatAgo(group.last_message_at) : '无消息'}</span>
            </div>
          </button>
        </li>
      ))}
    </ul>
  )
}

function MessageRow({ message, botNames }: { message: Message; botNames: Map<string, string> }) {
  const bot = botNames.get(message.sender_id)
  return (
    <li className={cn('px-4 py-2 text-sm', bot && 'bg-primary/5')}>
      <div className="flex items-baseline gap-2 text-xs text-muted-foreground">
        <span className={cn('font-medium text-foreground', bot && 'text-primary')}>
          {message.sender_name}
          {bot && '（bot）'}
        </span>
        <span>{formatTime(message.sent_at)}</span>
        {message.source === 'backfill' && <Badge variant="outline" className="h-4 px-1 text-[10px]">断线后补收</Badge>}
        {message.recalled_at && <Badge variant="outline" className="h-4 px-1 text-[10px]">已撤回</Badge>}
      </div>
      <p className={cn('whitespace-pre-wrap break-words', message.recalled_at && 'text-muted-foreground line-through')}>
        {message.text || '（空消息）'}
      </p>
    </li>
  )
}

type Row = { at: number; key: string; message?: Message; decision?: Decision }

function GroupTimeline({ group, botNames }: { group: Group; botNames: Map<string, string> }) {
  const { subscribe } = useLive()
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [error, setError] = useState('')
  const scroller = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)

  useEffect(() => {
    let cancelled = false
    stickToBottom.current = true
    getJson<Timeline>(`/api/groups/${encodeURIComponent(group.key)}/timeline`).then(
      (data) => !cancelled && (setTimeline(data), setError('')),
      (e: Error) => !cancelled && setError(`读取消息失败：${e.message}`),
    )
    return () => {
      cancelled = true
    }
  }, [group.key])

  useEffect(
    () =>
      subscribe((item) =>
        setTimeline((current) => {
          if (!current || item.kind === 'log' || item.kind === 'watch') return current
          if (item.kind === 'decision') {
            if (!current.channel_ids.includes(item.decision.channel_id)) return current
            if (current.decisions.some((d) => d.id === item.decision.id)) return current
            return { ...current, decisions: [...current.decisions, item.decision] }
          }
          // The same group message arrives once per bot account; show the viewer account's copy.
          if (item.message.channel_id !== current.viewer_channel_id) return current
          if (item.kind === 'recall') {
            return { ...current, messages: current.messages.map((m) => (m.id === item.message.id ? item.message : m)) }
          }
          if (current.messages.some((m) => m.id === item.message.id)) return current
          return { ...current, messages: [...current.messages, item.message] }
        }),
      ),
    [subscribe],
  )

  const rows = useMemo<Row[]>(() => {
    if (!timeline) return []
    const items: Row[] = [
      ...timeline.messages.map((m) => ({ at: m.sent_at, key: `m${m.id}`, message: m })),
      ...timeline.decisions.map((d) => ({ at: d.created_at, key: `d${d.id}`, decision: d })),
    ]
    return items.sort((a, b) => a.at - b.at)
  }, [timeline])

  useLayoutEffect(() => {
    const el = scroller.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [rows])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b px-4 py-3">
        <div className="font-medium">{groupTitle(group)}</div>
        <div className="text-xs text-muted-foreground">
          消息取自 {timeline?.viewer ?? '…'} 的视角 · 绿色卡片是 bot 本来会说的话（只看不说，不会真的发出去）
          · 没有卡片的消息表示没触发：没点名，也没到插话的时机
        </div>
      </div>
      <div
        ref={scroller}
        className="min-h-0 flex-1 overflow-y-auto py-1"
        onScroll={(e) => {
          const el = e.currentTarget
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
        }}
      >
        {error && <p className="px-4 py-2 text-sm text-destructive">{error}</p>}
        <ul>
          {rows.map((row) =>
            row.message ? (
              <MessageRow key={row.key} message={row.message} botNames={botNames} />
            ) : (
              <DecisionCard key={row.key} decision={row.decision!} />
            ),
          )}
        </ul>
      </div>
    </div>
  )
}

export function Messages() {
  const { channelsVersion } = useLive()
  const { data: bots } = useBots<Bot[]>('/api/bots')
  const [groups, setGroups] = useState<Group[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => {
      getJson<Group[]>('/api/groups').then(
        (rows) => {
          setGroups(rows)
          setError('')
          setSelected((current) => current ?? rows[0]?.key ?? null)
        },
        (e: Error) => setError(`读取群列表失败：${e.message}`),
      )
    }, 1000)
    return () => clearTimeout(timer)
  }, [channelsVersion])

  const botNames = useMemo(() => new Map((bots ?? []).map((b) => [b.self_id, b.name])), [bots])
  const group = groups.find((g) => g.key === selected) ?? null

  return (
    <div className="flex h-[calc(100svh-9rem)] min-h-96 overflow-hidden rounded-xl border bg-card">
      <aside className="w-64 shrink-0 overflow-y-auto border-r">
        {error && <p className="p-4 text-sm text-destructive">{error}</p>}
        <GroupList groups={groups} selected={selected} onSelect={setSelected} />
      </aside>
      {group ? (
        <GroupTimeline key={group.key} group={group} botNames={botNames} />
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">选一个群查看消息</div>
      )}
    </div>
  )
}
