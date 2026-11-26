import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowDown, Check, ChevronLeft, Paperclip, SendHorizontal, Settings2, Smile, Users, X } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { SentFiles, SentPictures, Tray } from '@/components/chat/Attachments'
import { MeGlyph } from '@/components/chat/MyAvatar'
import { Picture } from '@/components/chat/Picture'
import { TakeBack } from '@/components/chat/TakeBack'
import { StickerPicker } from '@/components/chat/StickerPicker'
import { Switch } from '@/components/ui/switch'
import { getJson, sendJson, type Message } from '@/lib/api'
import { rowsOf, timeLine } from '@/lib/chat-time'
import { picturesOf, wordsOf } from '@/lib/desk-media'
import { DROP_MAX, FILE_MAX, isPicture, useFileDrop } from '@/lib/file-drop'
import { FILES_MAX, filesOf, keepFile, stageFile, type Kept, type StagedFile } from '@/lib/group-files'
import { useSubscribe } from '@/lib/live'
import { STAGED_MAX, forget, lineOf, stage, upload, type Staged } from '@/lib/staged-pictures'
import { stickerImage, stickerOf } from '@/lib/sticker-packs'
import type { Companion, DeskGroup, GroupMember } from '@/lib/groups'
import { cn } from '@/lib/utils'

/** 桌面群: you and several companions in one chat. nest copies every message to every member;
 * this window shows the group through the first member's copy. */

const WAIT_MS = 90_000
const NEAR_BOTTOM = 90
const LIVELINESS: [string, string][] = [
  ['active', '话多'],
  ['occasional', '普通'],
  ['mentions', '安静'],
]
// A member's messages carry her account id (the part of her bot id after the platform).
const selfId = (botId: string) => botId.slice(botId.indexOf(':') + 1)

/** The group's mark: its first members' avatars, overlapping. */
export function GroupGlyph({ group, size = 'list' }: { group: DeskGroup; size?: 'list' | 'lg' }) {
  const shown = group.members.slice(0, 3)
  const box = size === 'lg' ? 'size-14' : 'size-12'
  return (
    <span aria-hidden className={cn('relative shrink-0', box)}>
      {shown.map((m, n) => (
        <BotGlyph
          key={m.bot_id}
          name={m.name}
          id={m.bot_id}
          size="sm"
          className={cn(
            'absolute size-[58%] ring-2 ring-card',
            n === 0 && 'top-0 left-0',
            n === 1 && 'top-0 right-0',
            n === 2 && 'bottom-0 left-1/2 -translate-x-1/2',
            shown.length === 2 && n === 1 && 'top-auto bottom-0',
          )}
        />
      ))}
    </span>
  )
}

const GroupBubble = memo(function GroupBubble({
  message,
  member,
  first,
  fresh,
  last,
  takeBack,
}: {
  message: Message
  member: GroupMember | null
  first: boolean
  fresh: boolean
  /** Your newest one: 「撤回」 shows on a phone too. */
  last: boolean
  takeBack: (message: Message) => Promise<void>
}) {
  const mine = member === null
  const sticker = stickerOf(message.segments)
  const pictures = picturesOf(message.segments)
  const files = filesOf(message.segments)
  const extras = pictures.length + files.length > 0
  const words = extras ? wordsOf(message.segments) : message.text || '（一条图片或别的消息）'
  if (mine && message.recalled_at && message.external_id.startsWith('app:')) {
    return <li className="mt-2 text-center text-[11px] text-muted-foreground">你撤回了一句话</li>
  }
  return (
    <li className={cn('group/bubble flex gap-2.5 px-4', mine && 'flex-row-reverse', first ? 'mt-3' : 'mt-1')}>
      {first ? (
        mine ? (
          <MeGlyph />
        ) : (
          <BotGlyph name={member.name} id={member.bot_id} size="chat" />
        )
      ) : (
        <span aria-hidden className="w-10 shrink-0" />
      )}
      <div className={cn('flex max-w-[72%] min-w-0 flex-col', mine && 'items-end')}>
        {first && !mine && <span className="px-1 pb-0.5 text-xs text-muted-foreground">{member.name}</span>}
        {sticker ? (
          <Picture
            src={stickerImage(sticker.id)}
            name={sticker.label}
            title={`${sticker.label} · ${timeLine(message.sent_at)}`}
          >
            <img
              src={stickerImage(sticker.id)}
              alt={sticker.label}
              loading="lazy"
              decoding="async"
              draggable={false}
              className={cn(
                'max-h-36 max-w-36 rounded-2xl object-contain',
                fresh && 'animate-in fade-in zoom-in-90 duration-200 ease-out motion-reduce:animate-none',
              )}
            />
          </Picture>
        ) : null}
        {pictures.length > 0 && <SentPictures names={pictures} title={timeLine(message.sent_at)} mine={mine} />}
        {files.length > 0 && (
          <div className={cn('max-w-full', pictures.length > 0 && 'mt-1.5')}>
            <SentFiles files={files} mine={mine} />
          </div>
        )}
        {!sticker && words && (
          <p
            title={timeLine(message.sent_at)}
            className={cn(
              'rounded-[20px] px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap break-words shadow-sm',
              mine ? 'bg-primary text-primary-foreground' : 'border bg-card text-card-foreground',
              first && (mine ? 'rounded-tr-md' : 'rounded-tl-md'),
              fresh && 'animate-in fade-in slide-in-from-bottom-1.5 duration-200 ease-out motion-reduce:animate-none',
              message.recalled_at && 'line-through opacity-60',
              extras && 'mt-1.5',
            )}
          >
            {words}
          </p>
        )}
      </div>
      {mine && (
        <span className="-mr-1.5 shrink-0 self-end pb-1">
          <TakeBack always={last} onClick={() => takeBack(message)} />
        </span>
      )}
    </li>
  )
})

type Sent = { key: number; text: string; words: string; thumbs: string[]; files: string[] }

const seen = new Map<number, Message[]>()
const memberOfIsNone = (m: Message, bySelf: ReadonlyMap<string, GroupMember>) => !m.from_self && !bySelf.has(m.sender_id)

export function GroupConversation({
  group,
  onSettings,
  onStickers,
  onBack,
}: {
  group: DeskGroup
  onSettings: () => void
  onStickers: () => void
  /** A phone: back to the list. */
  onBack?: () => void
}) {
  const subscribe = useSubscribe()
  const channel = group.view
  const bySelf = useMemo(() => new Map(group.members.map((m) => [selfId(m.bot_id), m])), [group.members])
  const viewer = group.members[0] ?? null
  const [messages, setMessages] = useState<Message[] | null>(() => (channel ? (seen.get(channel) ?? null) : null))
  const [arrived, setArrived] = useState<ReadonlySet<number>>(new Set())
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<Sent[]>([])
  const [sending, setSending] = useState(false)
  // Put by the box, sent together with the words: one message for everyone.
  const [pictures, setPictures] = useState<Staged[]>([])
  const [files, setFiles] = useState<StagedFile[]>([])
  const now = useRef({ pictures, files }) // as of now, for pictures that take a moment to get ready
  useEffect(() => {
    now.current = { pictures, files }
  }, [pictures, files])
  const kept = useRef(new WeakMap<File, Kept>()) // uploaded by a try that failed later
  const picker = useRef<HTMLInputElement>(null)
  const box = useRef<HTMLTextAreaElement>(null)
  const [waitingSince, setWaitingSince] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [picking, setPicking] = useState(false)
  const [away, setAway] = useState(0)
  const scroller = useRef<HTMLDivElement>(null)
  const placed = useRef(false)
  const atEnd = useRef(true)
  const rows = useMemo(() => rowsOf((messages ?? []).slice(-120)), [messages])

  useEffect(() => {
    if (channel === null) return
    let cancelled = false
    getJson<Message[]>(`/api/channels/${channel}/messages?limit=200`).then(
      (found) => !cancelled && (seen.set(channel, found), setMessages(found)),
      (e: Error) => !cancelled && setNote(`聊天记录没读出来，过一会儿再试试（${e.message}）`),
    )
    return () => {
      cancelled = true
    }
  }, [channel])

  useEffect(
    () =>
      subscribe((item) => {
        if (item.kind !== 'message' && item.kind !== 'recall') return
        const message = item.message
        if (message.channel_id !== channel) return
        const fromMember = message.from_self || bySelf.has(message.sender_id)
        if (fromMember) setWaitingSince(null)
        else setPending((list) => list.filter((p) => p.text !== message.text))
        setMessages((current) => {
          if (!current) return current
          const next = current.some((m) => m.id === message.id)
            ? current.map((m) => (m.id === message.id ? message : m))
            : [...current, message]
          seen.set(message.channel_id, next)
          return next
        })
        if (item.kind === 'message') {
          setArrived((ids) => new Set(ids).add(message.id))
          if (!atEnd.current && fromMember) setAway((n) => n + 1)
        }
      }),
    [subscribe, channel, bySelf],
  )

  useEffect(() => {
    if (waitingSince === null) return
    const timer = setTimeout(() => setWaitingSince(null), WAIT_MS)
    return () => clearTimeout(timer)
  }, [waitingSince])

  useLayoutEffect(() => {
    const el = scroller.current
    if (!el || messages === null) return
    if (!placed.current) {
      el.scrollTop = el.scrollHeight
      placed.current = true
      return
    }
    if (atEnd.current) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [messages, pending, waitingSince])

  const memberOf = (m: Message): GroupMember | null =>
    m.from_self ? viewer : (bySelf.get(m.sender_id) ?? null)

  const takeBack = useCallback(
    async (message: Message) => {
      await sendJson('DELETE', `/api/desk/groups/${group.id}/messages/${message.id}`)
    },
    [group.id],
  )
  const lastMine = useMemo(() => messages?.findLast((m) => memberOfIsNone(m, bySelf) && !m.recalled_at)?.id, [messages, bySelf])

  const post = async (body: { text: string } | { sticker_id: number }) => {
    setNote('')
    const mine = 'text' in body ? { key: Date.now(), text: body.text, words: body.text, thumbs: [], files: [] } : null
    if (mine) {
      setPending((list) => [...list, mine])
      setDraft('')
    }
    try {
      await sendJson('POST', `/api/desk/groups/${group.id}/messages`, body)
      setWaitingSince(Date.now())
    } catch (e) {
      if (mine) {
        setPending((list) => list.filter((p) => p.key !== mine.key))
        setDraft((current) => current || mine.text)
      }
      setNote(`这句没发出去，再试一次吧（${(e as Error).message}）`)
    }
  }
  const canSend = Boolean(draft.trim() || pictures.length || files.length) && !sending
  const send = async () => {
    if (!canSend) return
    const text = draft.trim()
    if (!pictures.length && !files.length) return post({ text })
    // Nothing is lost when it does not go out: the words, the pictures and the files are put back.
    const [shots, papers] = [pictures, files]
    const titles = papers.map((s) => s.file.name)
    const mine: Sent = {
      key: Date.now(),
      text: lineOf(shots.length, text, titles),
      words: text,
      thumbs: shots.map((s) => s.thumb),
      files: titles,
    }
    setSending(true)
    setNote('')
    setPending((list) => [...list, mine])
    setDraft('')
    setPictures([])
    setFiles([])
    try {
      const names: string[] = []
      for (const s of shots) names.push(await upload(`/api/desk/groups/${group.id}/pictures`, s.file))
      const placed: Kept[] = []
      for (const s of papers) {
        const known = kept.current.get(s.file) ?? (await keepFile(group.id, s.file))
        kept.current.set(s.file, known)
        placed.push({ name: known.name, title: known.title })
      }
      const done = await sendJson<{ problems: string[] }>('POST', `/api/desk/groups/${group.id}/messages`, {
        text,
        pictures: names,
        files: placed,
      })
      if (done.problems.length) setNote(done.problems.join('；'))
      setWaitingSince(Date.now())
    } catch (e) {
      shots.forEach((s) => forget(s.file))
      papers.forEach((s) => kept.current.delete(s.file))
      setPending((list) => list.filter((p) => p.key !== mine.key))
      setDraft((current) => [text, current].filter(Boolean).join('\n'))
      setPictures((current) => [...shots, ...current].slice(0, STAGED_MAX))
      setFiles((current) => [...papers, ...current].slice(0, FILES_MAX))
      setNote(`没发出去，再试一次吧（${(e as Error).message}）`)
    } finally {
      setSending(false)
    }
  }
  const takeFiles = async (given: File[], folders: boolean) => {
    const problems = folders ? ['文件夹放不进来，打包成 zip 再试'] : []
    if (given.length > DROP_MAX) problems.push(`一次最多 ${DROP_MAX} 个，后面的没有放`)
    const shots: Staged[] = []
    const papers: StagedFile[] = []
    for (const file of given.slice(0, DROP_MAX)) {
      if (!isPicture(file)) {
        if (file.size > FILE_MAX) problems.push(`「${file.name}」超过 50 MB`)
        else papers.push(stageFile(file))
        continue
      }
      try {
        shots.push(await stage(file))
      } catch {
        problems.push(`「${file.name}」这张图打不开`)
      }
    }
    const all = { pictures: [...now.current.pictures, ...shots], files: [...now.current.files, ...papers] }
    if (all.pictures.length > STAGED_MAX) problems.push(`一条消息最多 ${STAGED_MAX} 张图，多的没有放`)
    if (all.files.length > FILES_MAX) problems.push(`一条消息最多 ${FILES_MAX} 个文件，多的没有放`)
    now.current = { pictures: all.pictures.slice(0, STAGED_MAX), files: all.files.slice(0, FILES_MAX) }
    setPictures(now.current.pictures)
    setFiles(now.current.files)
    setNote(problems.join('；'))
    box.current?.focus()
  }
  const dropping = useFileDrop((given, folders) => void takeFiles(given, folders))
  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const found = [...e.clipboardData.files].filter((f) => f.type.startsWith('image/'))
    if (!found.length) return
    e.preventDefault()
    void takeFiles(found, false)
  }
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void send()
    }
  }

  return (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col" {...dropping.handlers}>
      {dropping.over && (
        <div className="pointer-events-none absolute inset-2 z-20 grid place-items-center rounded-2xl border-2 border-dashed border-primary bg-card/90 p-6 text-center">
          <div>
            <p className="font-medium">松手放到输入框上面</p>
            <p className="mt-1 text-xs text-muted-foreground">
              和话一起发。文件会放进群里每位伙伴的文件夹，文字内容大家都看得到
            </p>
          </div>
        </div>
      )}
      <div className="flex items-center gap-3 border-b bg-card/70 px-5 py-3 backdrop-blur max-sm:px-3">
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            aria-label="回到列表"
            className="-ml-1 grid size-9 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-muted active:scale-90"
          >
            <ChevronLeft className="size-5" />
          </button>
        )}
        <GroupGlyph group={group} size="lg" />
        <div className="min-w-0">
          <div className="text-lg font-semibold">{group.name}</div>
          <div className="truncate text-xs text-muted-foreground">
            你、{group.members.map((m) => m.name).join('、')}
            {group.created_by !== 'user' && ` · ${group.members.find((m) => m.bot_id === group.created_by)?.name ?? ''}拉的群`}
          </div>
        </div>
        <button
          type="button"
          onClick={onSettings}
          aria-label="群设置"
          title="群设置"
          className="ml-auto grid size-9 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground active:scale-95"
        >
          <Settings2 className="size-5" />
        </button>
      </div>
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          ref={scroller}
          onScroll={() => {
            const el = scroller.current
            if (!el) return
            atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM
            if (atEnd.current && away) setAway(0)
          }}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain pt-2 pb-4 [scrollbar-gutter:stable]"
          style={{
            backgroundImage:
              'radial-gradient(color-mix(in oklch, var(--border) 80%, transparent) 1px, transparent 1.2px)',
            backgroundSize: '22px 22px',
          }}
        >
          {messages?.length === 0 && (
            <div className="flex flex-col items-center gap-3 px-4 py-12 text-center text-sm text-muted-foreground">
              <GroupGlyph group={group} size="lg" />
              群建好啦，说句话，大家都会看到。
            </div>
          )}
          <ul>
            {rows.map((row) =>
              row.kind === 'time' ? (
                <li key={row.key} className="mt-4 flex justify-center">
                  <span className="rounded-full bg-muted/80 px-3 py-0.5 text-[11px] text-muted-foreground">
                    {timeLine(row.at)}
                  </span>
                </li>
              ) : (
                <GroupBubble
                  key={row.key}
                  message={row.message}
                  member={memberOf(row.message)}
                  first={row.first}
                  fresh={arrived.has(row.message.id)}
                  last={row.message.id === lastMine}
                  takeBack={takeBack}
                />
              ),
            )}
            {pending.map((p) => (
              <li key={p.key} className="mt-1 flex flex-row-reverse gap-2.5 px-4">
                <span aria-hidden className="w-10 shrink-0" />
                <div className="flex max-w-[72%] min-w-0 flex-col items-end gap-1.5">
                  {p.thumbs.length > 0 && (
                    <div className="flex flex-wrap justify-end gap-1.5 opacity-70">
                      {p.thumbs.map((src, i) => (
                        <img key={i} src={src} alt="图片" className="size-28 rounded-2xl border object-cover max-sm:size-24" />
                      ))}
                    </div>
                  )}
                  {p.files.map((name, i) => (
                    <span key={i} className="max-w-full truncate rounded-2xl border bg-card px-3 py-2 text-xs opacity-70">
                      {name}
                    </span>
                  ))}
                  {p.words && (
                    <p className="rounded-[20px] bg-primary px-4 py-2.5 text-[15px] whitespace-pre-wrap break-words text-primary-foreground opacity-70 shadow-sm">
                      {p.words}
                    </p>
                  )}
                  <span className="px-2 pt-0.5 text-[11px] text-muted-foreground">发送中…</span>
                </div>
              </li>
            ))}
          </ul>
          {waitingSince !== null && (
            <p role="status" className="mt-3 flex items-center gap-2 px-6 text-xs text-muted-foreground">
              <span className="flex gap-1">
                {[0, 150, 300].map((d) => (
                  <span
                    key={d}
                    style={{ animationDelay: `${d}ms` }}
                    className="size-1.5 animate-bounce rounded-full bg-muted-foreground/70 motion-reduce:animate-none"
                  />
                ))}
              </span>
              大家在看…
            </p>
          )}
          {note && (
            <p role="status" className="mx-auto mt-3 w-fit max-w-[85%] rounded-full bg-muted/80 px-3 py-1 text-xs text-muted-foreground">
              {note}
            </p>
          )}
        </div>
        {away > 0 && (
          <button
            type="button"
            onClick={() => {
              setAway(0)
              atEnd.current = true
              scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
            }}
            className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-primary px-3.5 py-1.5 text-xs font-medium text-primary-foreground shadow-lg animate-in fade-in slide-in-from-bottom-2 duration-200 active:scale-95"
          >
            <ArrowDown className="size-3.5" />
            {away} 条新消息
          </button>
        )}
      </div>
      <div className="px-4 pt-2 pb-[max(1rem,env(safe-area-inset-bottom))] max-sm:px-2">
        <Tray
          pictures={pictures}
          files={files}
          onPicture={(key) => setPictures((list) => list.filter((s) => s.key !== key))}
          onFile={(key) => setFiles((list) => list.filter((s) => s.key !== key))}
        />
        <div className="relative flex items-end gap-1 rounded-[26px] border bg-card p-1.5 shadow-sm focus-within:ring-2 focus-within:ring-ring">
          <button
            type="button"
            onClick={() => setPicking((v) => !v)}
            aria-label="发表情包"
            aria-expanded={picking}
            className={cn(
              'grid size-10 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-primary',
              picking && 'bg-muted text-primary',
            )}
          >
            <Smile className="size-5" />
          </button>
          {picking && viewer && (
            <StickerPicker
              botId={viewer.bot_id}
              botName={viewer.name}
              onPick={(id) => {
                setPicking(false)
                void post({ sticker_id: id })
              }}
              onManage={() => {
                setPicking(false)
                onStickers()
              }}
              onClose={() => setPicking(false)}
            />
          )}
          <button
            type="button"
            onClick={() => picker.current?.click()}
            aria-label="放图片和文件"
            title="放图片和文件（也可以粘贴图片，或者拖进来），和话一起发"
            className="grid size-10 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-primary"
          >
            <Paperclip className="size-5" />
          </button>
          <input
            ref={picker}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              const given = [...(e.target.files ?? [])]
              e.target.value = ''
              if (given.length) void takeFiles(given, false)
            }}
          />
          <textarea
            ref={box}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            rows={Math.min(6, Math.max(1, draft.split('\n').length))}
            maxLength={4000}
            placeholder={`在「${group.name}」里说点什么…`}
            title="Enter 发送，Shift+Enter 换行；叫名字她就会接"
            aria-label={`发到${group.name}的话`}
            className="min-h-10 min-w-0 flex-1 resize-none bg-transparent py-2 text-base outline-none placeholder:text-muted-foreground sm:text-[15px]"
          />
          <button
            type="button"
            onClick={() => void send()}
            disabled={!canSend}
            aria-label="发送"
            className="grid size-10 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground transition-[opacity,transform] active:scale-90 disabled:opacity-40"
          >
            <SendHorizontal className="size-4" />
          </button>
        </div>
      </div>
    </div>
  )
}

function MemberPicker({
  companions,
  chosen,
  onToggle,
}: {
  companions: Companion[]
  chosen: string[]
  onToggle: (botId: string) => void
}) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(7rem,1fr))] gap-2">
      {companions.map((c) => {
        const on = chosen.includes(c.bot_id)
        return (
          <button
            key={c.bot_id}
            type="button"
            onClick={() => onToggle(c.bot_id)}
            aria-pressed={on}
            className={cn(
              'relative flex flex-col items-center gap-1.5 rounded-2xl border p-3 text-sm transition-colors active:scale-[0.98]',
              on ? 'border-primary bg-primary/10' : 'hover:bg-muted/60',
            )}
          >
            <BotGlyph name={c.name} id={c.bot_id} size="list" />
            {c.name}
            {on && (
              <span className="absolute top-2 right-2 grid size-5 place-items-center rounded-full bg-primary text-primary-foreground">
                <Check className="size-3" />
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export function NewGroup({
  companions,
  onCancel,
  onDone,
}: {
  companions: Companion[]
  onCancel: () => void
  onDone: (group: DeskGroup) => void
}) {
  const [name, setName] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const toggle = (id: string) => setChosen((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]))
  const create = async () => {
    setBusy(true)
    setError('')
    try {
      onDone(await sendJson<DeskGroup>('POST', '/api/desk/groups', { name, bot_ids: chosen }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b px-5 py-4">
        <Users className="size-5 text-primary" />
        <h2 className="text-base font-semibold">建一个群</h2>
        <button
          type="button"
          onClick={onCancel}
          aria-label="关闭"
          className="ml-auto grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto p-6">
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">群名字</span>
          <input
            value={name}
            maxLength={20}
            onChange={(e) => setName(e.target.value)}
            placeholder="比如：吃货群、周末出去玩"
            className="w-full rounded-full border bg-background px-4 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <div className="space-y-1.5">
          <span className="text-sm font-medium">拉谁进来（至少两个）</span>
          <MemberPicker companions={companions} chosen={chosen} onToggle={toggle} />
        </div>
        <p className="text-xs text-muted-foreground">
          群只在桌面聊天里，不会发到任何平台。你说的话大家都看得到；没叫名字时会有一个人接，她们也会互相接话，连着说几句后会等你开口。
        </p>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <button
          type="button"
          disabled={busy || !name.trim() || chosen.length < 2}
          onClick={() => void create()}
          className="rounded-full bg-primary px-6 py-2 text-sm font-medium text-primary-foreground active:scale-[0.98] disabled:opacity-40"
        >
          建群
        </button>
      </div>
    </div>
  )
}

export function GroupSettings({
  group,
  companions,
  onClose,
  onDissolved,
}: {
  group: DeskGroup
  companions: Companion[]
  onClose: () => void
  onDissolved: () => void
}) {
  const [name, setName] = useState(group.name)
  const [error, setError] = useState('')
  const [dissolving, setDissolving] = useState(false)
  const members = group.members.map((m) => m.bot_id)
  const patch = async (body: Record<string, unknown>) => {
    setError('')
    try {
      await sendJson('PATCH', `/api/desk/groups/${group.id}`, body)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b px-5 py-4">
        <GroupGlyph group={group} />
        <h2 className="text-base font-semibold">群设置</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="回到群聊"
          className="ml-auto grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      <div className="flex-1 space-y-6 overflow-y-auto p-6">
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">群名字</span>
          <input
            value={name}
            maxLength={20}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => name.trim() && name !== group.name && void patch({ name })}
            className="w-full max-w-sm rounded-full border bg-background px-4 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <div className="space-y-1.5">
          <span className="text-sm font-medium">成员</span>
          <MemberPicker
            companions={companions}
            chosen={members}
            onToggle={(id) =>
              void patch({ bot_ids: members.includes(id) ? members.filter((x) => x !== id) : [...members, id] })
            }
          />
        </div>
        <div className="space-y-2">
          <span className="text-sm font-medium">她们在群里话多不多</span>
          {group.members.map((m) => (
            <div key={m.bot_id} className="flex items-center gap-3">
              <BotGlyph name={m.name} id={m.bot_id} size="md" />
              <span className="w-20 truncate text-sm">{m.name}</span>
              <div className="flex rounded-full border p-0.5">
                {LIVELINESS.map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => void patch({ activity: { [m.bot_id]: key } })}
                    aria-pressed={m.activity === key}
                    className={cn(
                      'rounded-full px-3 py-1 text-xs',
                      m.activity === key ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <p className="text-xs text-muted-foreground">话多：什么都可能接；普通：隔一会儿或有人提问才接；安静：叫到她才说。</p>
        </div>
        <div className="space-y-3">
          <label className="flex items-start gap-3">
            <Switch checked={Boolean(group.remember)} onCheckedChange={(on) => void patch({ remember: on })} />
            <span>
              <span className="text-sm font-medium">记住群里聊过的事</span>
              <span className="block text-xs text-muted-foreground">
                长期记忆：你的喜好、情况、和谁的约定，她们在这个群里都记得（「记忆」页能看能改）。
              </span>
            </span>
          </label>
          <label className="flex items-start gap-3">
            <Switch checked={Boolean(group.slang)} onCheckedChange={(on) => void patch({ slang: on })} />
            <span>
              <span className="text-sm font-medium">学这个群的黑话</span>
              <span className="block text-xs text-muted-foreground">你们自己的梗和叫法，她们会慢慢学会（「黑话」页能看能改）。</span>
            </span>
          </label>
        </div>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="border-t pt-5">
          <button
            type="button"
            onClick={() =>
              dissolving
                ? void sendJson('DELETE', `/api/desk/groups/${group.id}`).then(onDissolved, (e: Error) => setError(e.message))
                : setDissolving(true)
            }
            className={cn(
              'rounded-full px-5 py-2 text-sm',
              dissolving ? 'bg-destructive text-white' : 'border text-muted-foreground hover:text-destructive',
            )}
          >
            {dissolving ? '真的解散吗？聊天记录会留着' : '解散这个群'}
          </button>
        </div>
      </div>
    </div>
  )
}
