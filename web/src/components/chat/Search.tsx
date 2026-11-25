import { useEffect, useRef, useState } from 'react'
import { Search as Glass, X } from 'lucide-react'

import { getJson } from '@/lib/api'
import { shortTime } from '@/lib/chat-time'

type Found = { id: number; from_self: boolean; sender_name: string; text: string; sent_at: number }

const PAUSE_MS = 250 // after the last key, then it looks
const BEFORE = 16 // characters of a long message shown before the first word found

/** The part of a message around the first word found, so a long one shows why it is listed. */
function around(text: string, words: string[]): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  const at = Math.min(...words.map((w) => flat.toLowerCase().indexOf(w.toLowerCase())).filter((n) => n >= 0), flat.length)
  return at > BEFORE && at < flat.length ? `…${flat.slice(at - BEFORE)}` : flat
}

/** 搜索聊天: over the chat's top; a message picked is shown where it was said. */
export function Search({ botId, name, onPick, onClose }: {
  botId: string
  name: string
  onPick: (messageId: number) => void
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<{ for: string; items: Found[] } | null>(null)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const asked = query.trim()
  const words = asked.split(/\s+/).filter(Boolean)

  useEffect(() => input.current?.focus(), [])
  useEffect(() => {
    if (!asked) return
    let cancelled = false
    const timer = setTimeout(() => {
      getJson<{ items: Found[] }>(`/api/desk/${encodeURIComponent(botId)}/search?q=${encodeURIComponent(asked)}`).then(
        (data) => !cancelled && (setFound({ for: asked, items: data.items }), setError('')),
        (e: Error) => !cancelled && setError(`没找成：${e.message}`),
      )
    }, PAUSE_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [botId, asked])

  const items = asked && found?.for === asked ? found.items : null
  return (
    <div
      role="search"
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
      className="absolute inset-x-3 top-2 z-20 flex max-h-[70%] flex-col overflow-hidden rounded-3xl border bg-popover text-popover-foreground shadow-xl animate-in fade-in slide-in-from-top-2 duration-150 motion-reduce:animate-none"
    >
      <div className="flex items-center gap-2 px-4 py-2">
        <Glass className="size-4 shrink-0 text-muted-foreground" />
        <input
          ref={input}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          maxLength={100}
          placeholder={`找找和${name}说过的话`}
          aria-label="搜索这段聊天"
          className="min-w-0 flex-1 bg-transparent py-1 text-sm outline-none"
        />
        <button
          type="button"
          onClick={onClose}
          aria-label="关掉搜索"
          className="grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      {error && <p role="alert" className="border-t px-4 py-2 text-xs text-destructive">{error}</p>}
      {items && (
        <ul className="min-h-0 overflow-y-auto border-t p-1.5">
          {items.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                onClick={() => onPick(m.id)}
                className="w-full rounded-2xl px-3 py-2 text-left hover:bg-muted"
              >
                <span className="flex items-baseline justify-between gap-2 text-[11px] text-muted-foreground">
                  <span>{m.from_self ? name : '我'}</span>
                  <span>{shortTime(m.sent_at)}</span>
                </span>
                <span className="line-clamp-2 text-sm break-words">{around(m.text, words)}</span>
              </button>
            </li>
          ))}
          {items.length === 0 && <li className="px-3 py-4 text-center text-sm text-muted-foreground">没有找到</li>}
        </ul>
      )}
    </div>
  )
}
