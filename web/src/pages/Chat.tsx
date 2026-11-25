import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import {
  AlarmClock,
  ArrowDown,
  BookHeart,
  ChevronLeft,
  Ellipsis,
  FolderOpen,
  GripVertical,
  House,
  ImagePlus,
  ListChecks,
  LoaderCircle,
  Mic,
  RotateCcw,
  Search as SearchIcon,
  SendHorizontal,
  Smile,
  Square,
  Sticker,
  UserPlus,
  Users,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { ChatSettings } from '@/components/chat/ChatSettings'
import { Files } from '@/components/chat/Files'
import { Home } from '@/components/chat/Home'
import { GroupConversation, GroupGlyph, GroupSettings, NewGroup } from '@/components/chat/GroupChat'
import { Keepsakes } from '@/components/chat/Keepsakes'
import { MeGlyph } from '@/components/chat/MyAvatar'
import { NewCompanion } from '@/components/chat/NewCompanion'
import { Picture } from '@/components/chat/Picture'
import { Reminders } from '@/components/chat/Reminders'
import { RoomScene } from '@/components/chat/RoomScene'
import { Welcome } from '@/components/chat/Samples'
import { Search } from '@/components/chat/Search'
import { StickerPacks } from '@/components/chat/StickerPacks'
import { StickerPicker } from '@/components/chat/StickerPicker'
import { TakeBack } from '@/components/chat/TakeBack'
import { STATUS_LABEL, Tasks, useTasks } from '@/components/chat/Tasks'
import { getJson, sendBlob, sendJson, type BotMode, type Message } from '@/lib/api'
import { rowsOf, shortTime, timeLine } from '@/lib/chat-time'
import { bubbles } from '@/lib/desk-stream'
import { groupLine, type DeskGroup } from '@/lib/groups'
import type { LifeView } from '@/lib/life'
import { useLiveData } from '@/lib/live-data'
import { chatView, desktop } from '@/lib/desktop'
import { useBots, useLive, useSubscribe } from '@/lib/live'
import {
  canRecord,
  mediaUrl,
  picturesOf,
  play,
  playNext,
  readAloud,
  stop,
  useAutoRead,
  usePlaying,
  useRecorder,
  voiceOf,
  wordsOf,
} from '@/lib/desk-media'
import { DROP_MAX, FILE_MAX, isPicture, useDropGuard, useFileDrop } from '@/lib/file-drop'
import { useNarrow } from '@/lib/narrow'
import { STAGED_MAX, forget, lineOf, stage, upload, type Staged } from '@/lib/staged-pictures'
import { stickerImage, stickerOf } from '@/lib/sticker-packs'
import { cn } from '@/lib/utils'

/** 桌面聊天: each bot's private chat with you, seen from here. What you type here she answers here; what was
 * said on the platform (QQ…) shows up too, since it is the same chat.
 *
 * It looks like a game's chat panel: big round avatars on both sides, soft bubbles, the time
 * between messages only where a pause or a new day is, and new messages popping in. */

type DeskChat = { channel_id: number; peer: string; name: string; persona: string; desk_only: boolean }
/** The newest message of a chat: what the list shows under her name. */
type Last = { text: string; from_self: boolean; sent_at: number; sticker: boolean; sender_name?: string }
type DeskBot = {
  bot_id: string
  name: string
  mode: BotMode
  desk_only: boolean
  has_model: boolean
  channel_id: number | null
  chats: DeskChat[]
  last: Last | null
  drafts?: number // 回忆草稿 waiting for you to read them
}

const WAIT_MS = 90_000 // a searched answer may take this long; after it the "typing" line gives up
const NEAR_BOTTOM = 90 // px: reading this close to the end, a new message scrolls into view
const WINDOW = 80 // rows drawn at first; scrolling to the top draws this many more (a long chat stays light)
// Each chat's messages as last seen in this window: switching back shows them at once (then refreshes).
const seen = new Map<number, Message[]>()
const FIRST = 200 // messages read when a chat opens
const OLDER = 100 // read at a time when you scroll past the oldest one here
type Pending = { key: number; text: string; thumbs?: string[]; words?: string }
const typedHere = (m: Message) => m.external_id.startsWith('app:')
/** One of her answers given here (not a reminder, not a line of her own): she can say it another way. */
const answeredHere = (m: Message) => m.from_self && /^app:[0-9a-f]+$/.test(m.external_id)
/** Taken back here: yours leaves a grey line; hers (said another way) leaves nothing. */
const takenBack = (m: Message) => Boolean(m.recalled_at) && typedHere(m)
const FOUND_MS = 2500 // how long a message found by 搜索 stays lit

function cannotTalk(bot: DeskBot): string {
  if (bot.mode === 'paused') return `${bot.name}在休息（已暂停）。在 Bot 页让她回来，她就能接着陪你聊了。`
  if (!bot.has_model) return `${bot.name}还说不了话：在 Bot 页给她选一个模型，再改成「正式发言」。`
  if (bot.mode !== 'live') return `${bot.name}现在只听不说（只看不说）。在 Bot 页改成「正式发言」，她就会回你了。`
  return ''
}

const online = (bot: DeskBot) => bot.mode === 'live' && bot.has_model && bot.channel_id !== null

/** Under her name: 「离线」 when she will not answer, else the last thing said (like a chat app's list). */
function statusOf(bot: DeskBot, last: Last | null): string {
  if (bot.channel_id === null) return '还没有聊天'
  if (!online(bot)) return '离线'
  if (!last) return '还没说过话'
  const said = last.sticker ? '[表情]' : last.text.replace(/\s+/g, ' ').trim() || '[消息]'
  return last.from_self ? said : `我：${said}`
}

function BotList({ bots, lasts, selected, onSelect, onReorder, wide = false }: {
  bots: DeskBot[]
  /** A phone's list, the whole width: names and last lines shown. */
  wide?: boolean
  /** Newer than what the list was fetched with: channel id -> its newest message. */
  lasts: ReadonlyMap<number, Last>
  selected: string | null
  onSelect: (id: string) => void
  onReorder: (ids: string[]) => void
}) {
  const [dragging, setDragging] = useState<string | null>(null)
  const drop = (onto: string) => {
    if (!dragging || dragging === onto) return
    const ids = bots.map((b) => b.bot_id).filter((id) => id !== dragging)
    ids.splice(ids.indexOf(onto), 0, dragging)
    onReorder(ids)
  }
  return (
    <ul className="space-y-1 p-2">
      {bots.map((bot) => {
        const current = selected === bot.bot_id
        const last = (bot.channel_id !== null && lasts.get(bot.channel_id)) || bot.last
        return (
          <li
            key={bot.bot_id}
            draggable
            onDragStart={(e) => {
              setDragging(bot.bot_id)
              e.dataTransfer.effectAllowed = 'move'
            }}
            onDragEnd={() => setDragging(null)}
            onDragOver={(e) => dragging && e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              drop(bot.bot_id)
            }}
            className={cn('group relative', dragging === bot.bot_id && 'opacity-40')}
          >
            <button
              type="button"
              onClick={() => onSelect(bot.bot_id)}
              aria-current={current ? 'true' : undefined}
              title={bot.name}
              className={cn(
                'flex w-full items-center gap-3 rounded-2xl px-2.5 py-2 text-left transition-[background-color,box-shadow] duration-200',
                current ? 'bg-card shadow-sm ring-1 ring-border' : 'hover:bg-card/60',
              )}
            >
              <BotGlyph
                name={bot.name}
                id={bot.bot_id}
                size="list"
                online={online(bot)}
                className={cn('ring-2', current ? 'ring-primary/60' : 'ring-transparent')}
              />
              <span className={cn('min-w-0 flex-1', wide ? 'block' : 'hidden md:block')}>
                <span className="flex items-baseline gap-2">
                  <span className="truncate text-[15px] font-semibold">{bot.name}</span>
                  {online(bot) && last && (
                    <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{shortTime(last.sent_at)}</span>
                  )}
                </span>
                <span className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                  {!online(bot) && bot.channel_id !== null && (
                    <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-muted-foreground/40" />
                  )}
                  <span className="truncate">{statusOf(bot, last)}</span>
                </span>
              </span>
            </button>
            <GripVertical
              aria-hidden
              className="pointer-events-none absolute top-1/2 right-2 size-4 -translate-y-1/2 text-muted-foreground opacity-0 group-hover:opacity-60"
            />
          </li>
        )
      })}
    </ul>
  )
}

const Bubble = memo(function Bubble({
  message,
  botId,
  botName,
  first,
  fresh,
  found,
  last,
  takeBack,
  again,
}: {
  message: Message
  botId: string
  botName: string
  first: boolean
  fresh: boolean
  /** 搜索 brought you here. */
  found: boolean
  /** Your newest one, or the end of her newest answer: its button shows on a phone too. */
  last: boolean
  takeBack: (message: Message) => Promise<void>
  /** 重说 (only for her newest answer). */
  again: ((message: Message) => Promise<void>) | null
}) {
  const hers = message.from_self
  const own = hers && voiceOf(message.segments) !== null
  const sticker = stickerOf(message.segments)
  const pictures = picturesOf(message.segments)
  const words = pictures.length ? wordsOf(message.segments) : message.text || '（一条图片或别的消息）'
  if (takenBack(message)) {
    return (
      <li data-message={message.id} className="mt-2 text-center text-[11px] text-muted-foreground">
        你撤回了一句话
      </li>
    )
  }
  return (
    <li
      data-message={message.id}
      className={cn(
        'group/bubble flex gap-2.5 px-4 transition-colors duration-300',
        !hers && 'flex-row-reverse',
        first ? 'mt-3' : 'mt-1',
        found && 'bg-primary/10',
      )}
    >
      {first ? (
        hers ? (
          <BotGlyph name={botName} id={botId} size="chat" />
        ) : (
          <MeGlyph />
        )
      ) : (
        <span aria-hidden className="w-10 shrink-0" />
      )}
      <div className={cn('flex max-w-[72%] min-w-0 flex-col', !hers && 'items-end')}>
        {sticker ? (
          // A 表情包 goes out as itself, without a bubble.
          <Picture
            src={stickerImage(sticker.id)}
            name={sticker.label}
            title={`${sticker.label} · ${timeLine(message.sent_at)}`}
          >
            <img
              src={stickerImage(sticker.id)}
              alt={sticker.label}
              draggable={false}
              loading="lazy"
              decoding="async"
              onLoad={followEnd}
              className={cn(
                'max-h-36 max-w-36 rounded-2xl object-contain',
                fresh && 'animate-in fade-in zoom-in-90 duration-200 ease-out motion-reduce:animate-none',
                message.recalled_at && 'opacity-40',
              )}
            />
          </Picture>
        ) : pictures.length > 0 ? (
          <div className={cn('flex flex-wrap gap-1.5', !hers && 'justify-end')}>
            {pictures.map((picture) => (
              <Picture key={picture} src={mediaUrl(picture)} name={picture} title={timeLine(message.sent_at)}>
                <img
                  src={mediaUrl(picture)}
                  alt="图片"
                  draggable={false}
                  loading="lazy"
                  decoding="async"
                  onLoad={followEnd}
                  className={cn(
                    'rounded-2xl border shadow-sm',
                    pictures.length > 1 ? 'size-28 object-cover max-sm:size-24' : 'max-h-60 max-w-60 object-contain',
                    fresh && 'animate-in fade-in zoom-in-95 duration-200 ease-out motion-reduce:animate-none',
                  )}
                />
              </Picture>
            ))}
          </div>
        ) : null}
        {!sticker && words && (
          <p
            title={timeLine(message.sent_at)}
            className={cn(
              'rounded-[20px] px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap break-words shadow-sm',
              hers ? 'border bg-card text-card-foreground' : 'bg-primary text-primary-foreground',
              first && (hers ? 'rounded-tl-md' : 'rounded-tr-md'),
              fresh && 'animate-in fade-in slide-in-from-bottom-1.5 duration-200 ease-out motion-reduce:animate-none',
              message.recalled_at && 'line-through opacity-60',
              pictures.length > 0 && 'mt-1.5',
            )}
          >
            {words}
          </p>
        )}
        {own && <Voice botId={botId} message={message} />}
        {!typedHere(message) && <span className="px-2 pt-0.5 text-[11px] text-muted-foreground">来自平台</span>}
      </div>
      {/* Beside the bubble, not under it: a few short messages in a row stay close together. */}
      {hers ? (
        <span className="-ml-1.5 flex shrink-0 items-center self-end pb-1">
          {!own && !sticker && words && <Voice botId={botId} message={message} />}
          {again && <Again always={last} onClick={() => again(message)} />}
        </span>
      ) : (
        typedHere(message) && (
          <span className="-mr-1.5 shrink-0 self-end pb-1">
            <TakeBack always={last} onClick={() => takeBack(message)} />
          </span>
        )
      )}
    </li>
  )
})

/** A picture came in after the rows were placed: still at the end of the chat if you were. */
function followEnd(e: { currentTarget: HTMLElement }) {
  const box = e.currentTarget.closest<HTMLElement>('[data-chat-scroller]')
  if (box?.dataset.atEnd) box.scrollTop = box.scrollHeight
}

/** Her voice beside her words (a voice reply), or 「读出来」 for any of her lines. */
function Voice({ botId, message }: { botId: string; message: Message }) {
  const own = voiceOf(message.segments)
  const [made, setMade] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const playingNow = usePlaying()
  const file = own ?? made
  const on = file !== null && playingNow === file
  const click = async () => {
    if (on) return stop()
    setError('')
    let name = file
    if (name === null) {
      setBusy(true)
      try {
        name = await readAloud(botId, message.id)
        setMade(name)
      } catch (e) {
        setError((e as Error).message)
        return
      } finally {
        setBusy(false)
      }
    }
    play(name).catch(() => setError('没放出来'))
  }
  const Icon = busy ? LoaderCircle : on ? Square : Volume2
  return (
    <span className={cn('flex items-center gap-1.5', own && 'px-1 pt-1')}>
      <button
        type="button"
        onClick={() => void click()}
        disabled={busy}
        aria-label={on ? '停下' : own ? '听她说' : '读出来'}
        title={on ? '停下' : own ? '听她说' : '读出来'}
        className={cn(
          'flex items-center gap-1 rounded-full text-[11px] text-muted-foreground transition-[opacity,color] hover:text-primary',
          own
            ? 'border bg-card px-2.5 py-1 shadow-sm'
            : 'p-1 opacity-60 group-hover/bubble:opacity-100 md:opacity-0 md:focus-visible:opacity-100',
          on && 'text-primary opacity-100 md:opacity-100',
        )}
      >
        <Icon className={cn('size-3.5', busy && 'animate-spin', on && !busy && 'fill-current')} />
        {own && (on ? '正在说' : '语音')}
      </button>
      {error && <span className="text-[11px] text-destructive">{error}</span>}
    </span>
  )
}

/** 重说: she takes her newest answer back and says it another way. */
function Again({ always, onClick }: { always: boolean; onClick: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => {
        setBusy(true)
        void onClick().finally(() => setBusy(false))
      }}
      aria-label="让她重说一遍"
      title="让她重说一遍"
      className={cn(
        'rounded-full p-1 text-muted-foreground transition-[opacity,color] hover:text-primary disabled:opacity-40',
        always ? 'opacity-60 group-hover/bubble:opacity-100' : 'opacity-0 group-hover/bubble:opacity-100 focus-visible:opacity-100',
      )}
    >
      <RotateCcw className={cn('size-3.5', busy && 'animate-spin')} />
    </button>
  )
}

/** What you just sent, shown before nest has it back (a moment over the network). */
function Sending({ text, thumbs = [] }: { text: string; thumbs?: string[] }) {
  return (
    <li className="mt-1 flex flex-row-reverse gap-2.5 px-4">
      <span aria-hidden className="w-10 shrink-0" />
      <div className="flex max-w-[72%] min-w-0 flex-col items-end">
        {thumbs.length > 0 && (
          <div className="flex flex-wrap justify-end gap-1.5 opacity-70">
            {thumbs.map((src, i) => (
              <img key={i} src={src} alt="图片" className="size-28 rounded-2xl border object-cover shadow-sm max-sm:size-24" />
            ))}
          </div>
        )}
        {text && (
          <p
            className={cn(
              'rounded-[20px] bg-primary px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap break-words text-primary-foreground opacity-70 shadow-sm',
              thumbs.length > 0 && 'mt-1.5',
            )}
          >
            {text}
          </p>
        )}
        <span className="px-2 pt-0.5 text-[11px] text-muted-foreground">发送中…</span>
      </div>
    </li>
  )
}

/** Her avatar and three hopping dots while she thinks of what to say. */
function Typing({ bot }: { bot: DeskBot }) {
  return (
    <div role="status" aria-label={`${bot.name} 正在想怎么回`} className="mt-3 flex gap-2.5 px-4">
      <BotGlyph name={bot.name} id={bot.bot_id} size="chat" />
      <span className="flex items-center gap-1 rounded-[20px] rounded-tl-md border bg-card px-4 py-3.5 shadow-sm">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            style={{ animationDelay: `${delay}ms` }}
            className="size-1.5 animate-bounce rounded-full bg-muted-foreground/70 motion-reduce:animate-none"
          />
        ))}
      </span>
    </div>
  )
}

/** Her answer while she is still writing it (nest.core.desk_stream). */
function Writing({ bot, text }: { bot: DeskBot; text: string }) {
  const parts = bubbles(text)
  return (
    <div aria-hidden className="mt-3">
      {parts.map((part, n) => (
        <div key={n} className={cn('flex gap-2.5 px-4', n > 0 && 'mt-1')}>
          {n === 0 ? <BotGlyph name={bot.name} id={bot.bot_id} size="chat" /> : <span className="w-10 shrink-0" />}
          <p
            className={cn(
              'max-w-[72%] min-w-0 rounded-[20px] border bg-card px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap break-words text-card-foreground shadow-sm',
              n === 0 && 'rounded-tl-md',
            )}
          >
            {part}
            {n === parts.length - 1 && (
              <span className="ml-0.5 inline-block h-4 w-0.5 translate-y-0.5 animate-pulse bg-primary motion-reduce:animate-none" />
            )}
          </p>
        </div>
      ))}
    </div>
  )
}

type MenuItem = { label: string; icon: typeof ListChecks; badge?: number; onClick: () => void }

/** 任务 / 文件 / 提醒 / 回忆档案, out of the way behind 「⋯」. */
function MoreMenu({ items }: { items: MenuItem[] }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false)
    const escape = (e: globalThis.KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [open])
  const busy = items.reduce((n, item) => n + (item.badge ?? 0), 0)
  return (
    <div ref={box} className="relative ml-auto shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="更多"
        aria-expanded={open}
        className="relative grid size-9 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        <Ellipsis className="size-5" />
        {busy > 0 && (
          <span className="absolute -top-0.5 -right-0.5 grid min-w-4 place-items-center rounded-full bg-primary px-1 text-[10px] text-primary-foreground">
            {busy}
          </span>
        )}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute top-full right-0 z-20 mt-1 w-40 overflow-hidden rounded-2xl border bg-popover p-1 text-popover-foreground shadow-lg animate-in fade-in zoom-in-95 duration-150 motion-reduce:animate-none"
        >
          {items.map(({ label, icon: Icon, badge, onClick }) => (
            <button
              key={label}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                onClick()
              }}
              className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-sm hover:bg-muted"
            >
              <Icon className="size-4 text-muted-foreground" />
              {label}
              {!!badge && (
                <span className="ml-auto rounded-full bg-primary px-1.5 text-[10px] text-primary-foreground">{badge}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Conversation({ bot, channel, activity, room, onKeepsakes, onReminders, onTasks, onFiles, onStickers, onBack }: {
  bot: DeskBot
  /** A phone: back to the list. */
  onBack?: () => void
  channel: number
  /** What she is doing in the 小窝 now (「在书房复习 · 专心」), when 她们的日子 is on. */
  activity: string
  /** The room she is in, drawn faintly behind the chat ('': none). */
  room: string
  onKeepsakes: () => void
  onReminders: () => void
  onTasks: () => void
  onFiles: () => void
  onStickers: () => void
}) {
  const subscribe = useSubscribe()
  const working = (useTasks(bot.bot_id).tasks ?? []).filter((t) =>
    ['running', 'queued', 'waiting_pc'].includes(t.status),
  )
  const [messages, setMessages] = useState<Message[] | null>(() => seen.get(channel) ?? null)
  const [pending, setPending] = useState<Pending[]>([])
  // Messages that came in while the chat was open pop in; the history loaded with it does not.
  const [arrived, setArrived] = useState<ReadonlySet<number>>(new Set())
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  // Why she did not answer, in nest's own words: shown only when you ask (「为什么？」).
  const [why, setWhy] = useState('')
  const [showWhy, setShowWhy] = useState(false)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [picking, setPicking] = useState(false)
  const [searching, setSearching] = useState(false)
  const [found, setFound] = useState<number | null>(null)
  const [waitingSince, setWaitingSince] = useState<number | null>(null)
  const [writing, setWriting] = useState('') // her answer so far
  const wrote = useRef(false) // it was seen being written: it does not pop in again
  const scroller = useRef<HTMLDivElement>(null)
  const placed = useRef(false) // scrolled to the end once (the history is in)
  const atEnd = useRef(true) // reading at the end of the chat (new messages follow)
  const [shown, setShown] = useState(WINDOW)
  const [away, setAway] = useState(0) // new messages that came while you were reading further up
  const kept = useRef<{ height: number; top: number } | null>(null) // where you were, as older rows go in
  // Older than what was read: 'more' to fetch when you get to the top, 'done' when there is no more.
  const [older, setOlder] = useState<'more' | 'loading' | 'done'>(() =>
    (seen.get(channel)?.length ?? FIRST) < FIRST ? 'done' : 'more',
  )
  const [autoRead, setAutoRead] = useAutoRead()
  const reading = useRef(autoRead)
  useEffect(() => {
    reading.current = autoRead
  }, [autoRead])
  const picker = useRef<HTMLInputElement>(null)
  const box = useRef<HTMLTextAreaElement>(null)
  const [staged, setStaged] = useState<Staged[]>([]) // pictures put by the box, sent with the words
  const stagedNow = useRef(staged) // as of now, for pictures that take a moment to get ready
  useEffect(() => {
    stagedNow.current = staged
  }, [staged])
  const listenReady = useLiveData<{ listen?: { ready: boolean } }>('/api/speech', []).data?.listen?.ready ?? false
  const recorder = useRecorder((text) => setDraft((d) => (d.trim() ? `${d.trimEnd()} ${text}` : text)))
  const blocked = cannotTalk(bot)
  const all = useMemo(() => rowsOf((messages ?? []).filter((m) => !(takenBack(m) && m.from_self))), [messages])
  // What can be taken back or said another way: your newest message, and her answers after it.
  const here = useMemo(() => (messages ?? []).filter((m) => !takenBack(m)), [messages])
  const lastMine = here.findLast((m) => !m.from_self)
  const answers = useMemo(() => {
    const at = here.findLastIndex((m) => !m.from_self)
    return new Set(at >= 0 && typedHere(here[at]) ? here.slice(at + 1).filter(answeredHere).map((m) => m.id) : [])
  }, [here])
  const lastAnswer = here.findLast((m) => answers.has(m.id))?.id
  const rows = all.length > shown ? all.slice(all.length - shown) : all

  useEffect(() => {
    let cancelled = false
    getJson<Message[]>(`/api/channels/${channel}/messages?limit=${FIRST}`).then(
      (rows) => {
        if (cancelled) return
        // Kept: what was read further back while the chat was open before.
        const back = (seen.get(channel) ?? []).filter((m) => rows.length > 0 && m.id < rows[0].id)
        const all = [...back, ...rows]
        seen.set(channel, all)
        setMessages(all)
        setError('')
        if (!back.length && rows.length < FIRST) setOlder('done')
      },
      (e: Error) => !cancelled && setError(`聊天记录没读出来，过一会儿再试试（${e.message}）`),
    )
    return () => {
      cancelled = true
    }
  }, [channel])

  useEffect(
    () =>
      subscribe((item) => {
        if (item.kind === 'decision') {
          if (item.decision.channel_id !== channel) return
          setWaitingSince(null)
          setWriting('')
          wrote.current = false
          // She decided, and nothing came of it: say why instead of leaving the question hanging.
          if (item.decision.outcome !== 'replied') {
            setNote(`${bot.name}这次没接上话`)
            setWhy(item.decision.reason)
            setShowWhy(false)
          }
          return
        }
        if (item.kind === 'desk_delta') {
          if (item.channelId !== channel) return
          setWriting(item.text)
          wrote.current = wrote.current || item.text !== ''
          if (item.text) setWaitingSince((since) => since && Date.now())
          return
        }
        if (item.kind !== 'message' && item.kind !== 'recall') return
        const message = item.message
        if (message.channel_id !== channel) return
        if (message.from_self && item.kind === 'message') setWriting('')
        if (item.kind === 'recall') {
          if (!message.from_self) setWaitingSince(null)
        } else if (message.from_self) setWaitingSince(null)
        // Yours came back: it takes the place of the 「发送中」 one.
        else setPending((list) => list.filter((p) => p.text !== message.text))
        setMessages((current) => {
          if (!current) return current
          const next = current.some((m) => m.id === message.id)
            ? current.map((m) => (m.id === message.id ? message : m))
            : [...current, message]
          seen.set(channel, next)
          return next
        })
        if (item.kind === 'message') {
          if (!(wrote.current && message.from_self)) setArrived((ids) => new Set(ids).add(message.id))
          if (!atEnd.current && message.from_self) setAway((n) => n + 1)
          if (reading.current && message.from_self && message.text && document.visibilityState === 'visible') {
            const own = voiceOf(message.segments)
            const said = own ? Promise.resolve(own) : readAloud(bot.bot_id, message.id)
            said.then(playNext).catch((e: Error) => {
              setNote(`${bot.name}的声音没放出来`)
              setWhy(e.message)
            })
          }
        }
      }),
    [subscribe, channel, bot.name, bot.bot_id],
  )

  useEffect(() => {
    if (waitingSince === null) return
    const timer = setTimeout(() => {
      setWaitingSince(null)
      setNote(`${bot.name}好像在忙，过一会儿再找她试试吧`)
      setWhy('等了一分半钟还没有回音。想知道她是怎么判断的，可以去控制台的「消息」页看看。')
    }, WAIT_MS)
    return () => clearTimeout(timer)
  }, [waitingSince, bot.name])

  // The history jumps to its end at once; afterwards new things glide into view, unless you are reading
  // further up.
  useLayoutEffect(() => {
    const el = scroller.current
    if (!el || messages === null) return
    if (!placed.current) {
      if (el.clientHeight === 0) return // hidden (a phone's list in front): placed when it shows
      el.scrollTop = el.scrollHeight
      placed.current = true
      return
    }
    if (atEnd.current) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [messages, pending, waitingSince, note])
  useLayoutEffect(() => {
    const el = scroller.current
    if (el && writing && atEnd.current) el.scrollTop = el.scrollHeight
  }, [writing])
  // Shown after being hidden, or the window resized: still at the end if you were.
  const loaded = messages !== null
  useEffect(() => {
    const el = scroller.current
    if (!el || !loaded) return
    const watch = new ResizeObserver(() => {
      if (el.clientHeight === 0 || !atEnd.current) return
      el.scrollTop = el.scrollHeight
      placed.current = true
    })
    watch.observe(el) // the box, not what is in it: new messages glide in on their own (above)
    return () => watch.disconnect()
  }, [loaded])
  // Older rows drawn above: you stay on what you were reading.
  useLayoutEffect(() => {
    const el = scroller.current
    if (!el || !kept.current) return
    el.scrollTop = kept.current.top + (el.scrollHeight - kept.current.height)
    kept.current = null
  }, [shown])
  const onScroll = () => {
    const el = scroller.current
    if (!el) return
    atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM
    el.dataset.atEnd = atEnd.current ? '1' : ''
    if (atEnd.current && away) setAway(0)
    if (el.scrollTop < 40 && all.length > shown) {
      kept.current = { height: el.scrollHeight, top: el.scrollTop }
      setShown((n) => n + WINDOW)
    } else if (el.scrollTop < 40 && older === 'more' && messages?.length) {
      void loadOlder(messages[0].id)
    }
  }
  // Further back than what was read: from nest, a page at a time, staying on what you were reading.
  const loadOlder = async (before: number) => {
    setOlder('loading')
    try {
      const got = await getJson<Message[]>(`/api/channels/${channel}/messages?limit=${OLDER}&before=${before}`)
      const el = scroller.current
      if (el) kept.current = { height: el.scrollHeight, top: el.scrollTop }
      setMessages((current) => {
        const known = new Set((current ?? []).map((m) => m.id))
        const next = [...got.filter((m) => !known.has(m.id)), ...(current ?? [])]
        seen.set(channel, next)
        return next
      })
      setShown((n) => n + got.length * 2) // a row per message, and at most one time line before each
      setOlder(got.length < OLDER ? 'done' : 'more')
    } catch {
      setOlder('more')
    }
  }
  // 搜索: to where it was said, reading further back as needed.
  const goTo = async (id: number) => {
    setSearching(false)
    let list = messages ?? []
    try {
      while (list.length > 0 && list[0].id > id) {
        const got = await getJson<Message[]>(`/api/channels/${channel}/messages?limit=${FIRST}&before=${list[0].id}`)
        if (got.length === 0) break
        list = [...got, ...list]
      }
    } catch (e) {
      setNote('没翻到那里，过一会儿再试试')
      setWhy((e as Error).message)
      return
    }
    atEnd.current = false
    setMessages((current) => {
      const known = new Set(list.map((m) => m.id))
      const next = [...list, ...(current ?? []).filter((m) => !known.has(m.id))]
      seen.set(channel, next)
      return next
    })
    setShown((n) => Math.max(n, list.length * 2))
    setFound(id)
  }
  useLayoutEffect(() => {
    if (found === null) return
    scroller.current?.querySelector(`[data-message="${found}"]`)?.scrollIntoView({ block: 'center' })
    const timer = setTimeout(() => setFound(null), FOUND_MS)
    return () => clearTimeout(timer)
  }, [found])
  const takeBack = useCallback(
    async (message: Message) => {
      try {
        await sendJson('DELETE', `/api/desk/${encodeURIComponent(bot.bot_id)}/messages/${message.id}`)
      } catch (e) {
        setNote('没撤回成')
        setWhy((e as Error).message)
      }
    },
    [bot.bot_id],
  )
  const again = useCallback(
    async (message: Message) => {
      setNote('')
      setWhy('')
      try {
        await sendJson('POST', `/api/desk/${encodeURIComponent(bot.bot_id)}/messages/${message.id}/retry`)
        setWaitingSince(Date.now())
      } catch (e) {
        setNote(`现在没法让${bot.name}重说`)
        setWhy((e as Error).message)
      }
    },
    [bot.bot_id, bot.name],
  )
  const toEnd = () => {
    setAway(0)
    atEnd.current = true
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }

  const post = async (body: { text: string } | { sticker_id: number }) => {
    setSending(true)
    setNote('')
    setWhy('')
    // Words show at once and the box empties; nest's copy replaces them when it comes back.
    const mine = 'text' in body ? { key: Date.now(), text: body.text } : null
    if (mine) {
      setPending((list) => [...list, mine])
      setDraft('')
    }
    try {
      await sendJson('POST', `/api/desk/${encodeURIComponent(bot.bot_id)}/messages`, body)
      setWaitingSince(Date.now())
    } catch (e) {
      if (mine) {
        setPending((list) => list.filter((p) => p.key !== mine.key))
        setDraft((current) => current || mine.text) // what was typed is not lost
      }
      setNote('这句没发出去，再试一次吧')
      setWhy((e as Error).message)
    } finally {
      setSending(false)
    }
  }
  const send = async () => {
    const text = draft.trim()
    if ((!text && !staged.length) || sending || blocked) return
    if (staged.length) await sendWithPictures(text, staged)
    else await post({ text })
  }
  // The pictures and the words are one message: she answers once. Nothing is lost when it does not go out.
  const sendWithPictures = async (text: string, pictures: Staged[]) => {
    const mine = { key: Date.now(), text: lineOf(pictures.length, text), words: text, thumbs: pictures.map((s) => s.thumb) }
    setSending(true)
    setNote('')
    setWhy('')
    setPending((list) => [...list, mine])
    setDraft('')
    setStaged([])
    try {
      const names: string[] = []
      for (const s of pictures) names.push(await upload(`/api/desk/${encodeURIComponent(bot.bot_id)}/pictures?keep=true`, s.file))
      await sendJson('POST', `/api/desk/${encodeURIComponent(bot.bot_id)}/messages`, { text, pictures: names })
      setWaitingSince(Date.now())
    } catch (e) {
      pictures.forEach((s) => forget(s.file))
      setPending((list) => list.filter((p) => p.key !== mine.key))
      setDraft((current) => [text, current].filter(Boolean).join('\n'))
      setStaged((current) => [...pictures, ...current].slice(0, STAGED_MAX))
      setNote('没发出去，再试一次吧')
      setWhy((e as Error).message)
    } finally {
      setSending(false)
    }
  }
  // Pictures wait by the box until you send; other files go into her folder, and the box starts a line about
  // them: you say what they are for.
  const takeFiles = async (files: File[], folders: boolean) => {
    if (blocked) {
      setNote('现在还不能发给她')
      setWhy(blocked)
      return
    }
    const problems = folders ? ['文件夹放不进来，打包成 zip 再试'] : []
    if (files.length > DROP_MAX) problems.push(`一次最多 ${DROP_MAX} 个，后面的没有放`)
    setNote('')
    setWhy('')
    setShowWhy(false)
    const pictures: Staged[] = []
    const others: File[] = []
    for (const file of files.slice(0, DROP_MAX)) {
      if (!isPicture(file)) {
        others.push(file)
        continue
      }
      try {
        pictures.push(await stage(file))
      } catch {
        problems.push(`「${file.name}」这张图打不开`)
      }
    }
    if (pictures.length) {
      const all = [...stagedNow.current, ...pictures]
      if (all.length > STAGED_MAX) problems.push(`一条消息最多 ${STAGED_MAX} 张图，多的没有放`)
      stagedNow.current = all.slice(0, STAGED_MAX)
      setStaged(stagedNow.current)
    }
    const placed: string[] = []
    if (others.length) setNote('正在放进她的文件夹…')
    for (const file of others) {
      if (file.size > FILE_MAX) {
        problems.push(`「${file.name}」超过 50 MB`)
        continue
      }
      try {
        const name = encodeURIComponent(file.name)
        const saved = await sendBlob<{ path: string }>(
          `/api/workspaces/${encodeURIComponent(bot.bot_id)}/upload?name=${name}`,
          file,
        )
        placed.push(saved.path)
      } catch (e) {
        problems.push(`「${file.name}」${(e as Error).message}`)
      }
    }
    if (placed.length) {
      const line = `我把${placed.map((path) => `「${path}」`).join('、')}放进你的文件夹了，`
      setDraft((current) => (current.trim() ? `${current.trimEnd()}\n${line}` : line))
    }
    if (placed.length || pictures.length) box.current?.focus()
    const done = placed.length ? `${placed.length} 个文件放好了，写上要${bot.name}做什么再发` : ''
    setNote([done, problems.length ? '有的没放进来' : ''].filter(Boolean).join('；'))
    setWhy(problems.join('；'))
  }
  const dropping = useFileDrop((files, folders) => void takeFiles(files, folders))
  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = [...e.clipboardData.files].filter((f) => f.type.startsWith('image/'))
    if (!files.length) return
    e.preventDefault()
    void takeFiles(files, false)
  }
  const sendSticker = (stickerId: number) => {
    setPicking(false)
    if (!sending && !blocked) void post({ sticker_id: stickerId })
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, Shift+Enter breaks the line; never while an input method is still composing.
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
            <p className="font-medium">{blocked ? '现在还不能发给她' : `松手就给${bot.name}`}</p>
            {!blocked && (
              <p className="mt-1 text-xs text-muted-foreground">图片先放在输入框上面，和话一起发；别的文件放进她的文件夹「收到的」</p>
            )}
          </div>
        </div>
      )}
      <div className="relative z-10 flex items-center gap-3 border-b bg-card/70 px-5 py-3 backdrop-blur max-sm:px-3">
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
        <BotGlyph name={bot.name} id={bot.bot_id} size="lg" online={bot.mode === 'live'} breathing />
        <div className="min-w-0">
          <div className="text-lg font-semibold">{bot.name}</div>
          <div className="truncate text-xs text-muted-foreground">
            {activity ||
              (bot.desk_only
                ? '她只在桌面上：在这里和桌面端跟她聊。'
                : '和她的私聊是同一段对话：在这里说的她在这里回，不会发到平台上；平台上说过的这里也看得到。')}
          </div>
        </div>
        <MoreMenu
          items={[
            { label: '搜索聊天', icon: SearchIcon, onClick: () => setSearching(true) },
            { label: '任务', icon: ListChecks, badge: working.length, onClick: onTasks },
            { label: '文件', icon: FolderOpen, onClick: onFiles },
            { label: '提醒', icon: AlarmClock, onClick: onReminders },
            { label: '回忆档案', icon: BookHeart, badge: bot.drafts ?? 0, onClick: onKeepsakes },
            {
              label: autoRead ? '自动读出来：开' : '自动读出来：关',
              icon: autoRead ? Volume2 : VolumeX,
              onClick: () => setAutoRead(!autoRead),
            },
          ]}
        />
      </div>
      {working.length > 0 && (
        <button
          type="button"
          onClick={onTasks}
          className="flex items-center gap-2 border-b bg-muted/40 px-5 py-1.5 text-left text-xs text-muted-foreground hover:text-foreground"
        >
          <ListChecks className="size-3.5 shrink-0" />
          <span className="truncate">
            {working.map((t) => `${STATUS_LABEL[t.status]}：${t.title}（${t.steps} 步）`).join('；')}
          </span>
        </button>
      )}
      <div className="relative flex min-h-0 flex-1 flex-col">
      {searching && (
        <Search
          botId={bot.bot_id}
          name={bot.name}
          onPick={(id) => void goTo(id)}
          onClose={() => setSearching(false)}
        />
      )}
      {room && <RoomScene room={room} className="text-foreground/[0.06] dark:text-foreground/[0.05]" />}
      <div
        ref={scroller}
        onScroll={onScroll}
        data-chat-scroller=""
        data-at-end="1"
        className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain pt-2 pb-4 [scrollbar-gutter:stable]"
        style={{
          backgroundImage:
            'radial-gradient(color-mix(in oklch, var(--border) 80%, transparent) 1px, transparent 1.2px)',
          backgroundSize: '22px 22px',
        }}
      >
        {error && <p className="px-4 py-2 text-sm text-destructive">{error}</p>}
        {older === 'loading' && (
          <p className="flex justify-center py-2 text-[11px] text-muted-foreground">
            <LoaderCircle className="mr-1 size-3.5 animate-spin" />
            翻更早的聊天…
          </p>
        )}
        {older === 'done' && !!messages?.length && all.length <= shown && (
          <p className="py-2 text-center text-[11px] text-muted-foreground/70">再往前就没有了</p>
        )}
        {messages?.length === 0 && (
          <div className="flex flex-col items-center gap-3 px-4 py-12 text-center text-sm text-muted-foreground">
            <BotGlyph name={bot.name} id={bot.bot_id} size="lg" />
            还没有说过话。跟{bot.name}打个招呼吧。
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
              <Bubble
                key={row.key}
                message={row.message}
                botId={bot.bot_id}
                botName={bot.name}
                first={row.first}
                fresh={arrived.has(row.message.id)}
                found={row.message.id === found}
                last={row.message.id === lastMine?.id || row.message.id === lastAnswer}
                takeBack={takeBack}
                again={waitingSince === null && !writing && !blocked && answers.has(row.message.id) ? again : null}
              />
            ),
          )}
          {pending.map((p) => (
            <Sending key={p.key} text={p.words ?? p.text} thumbs={p.thumbs} />
          ))}
        </ul>
        {writing ? <Writing bot={bot} text={writing} /> : waitingSince !== null && <Typing bot={bot} />}
        {note && (
          <div role="status" className="mx-auto mt-3 w-fit max-w-[85%] text-center text-xs text-muted-foreground">
            <p className="rounded-full bg-muted/80 px-3 py-1">
              {note}
              {why && (
                <button
                  type="button"
                  onClick={() => setShowWhy((v) => !v)}
                  className="ml-1.5 text-primary underline-offset-2 hover:underline"
                >
                  {showWhy ? '收起' : '为什么？'}
                </button>
              )}
            </p>
            {showWhy && why && <p className="mt-1 px-3 text-[11px] text-muted-foreground/80">{why}</p>}
          </div>
        )}
      </div>
      {away > 0 && (
        <button
          type="button"
          onClick={toEnd}
          className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-primary px-3.5 py-1.5 text-xs font-medium text-primary-foreground shadow-lg animate-in fade-in slide-in-from-bottom-2 duration-200 active:scale-95 motion-reduce:animate-none"
        >
          <ArrowDown className="size-3.5" />
          {away} 条新消息
        </button>
      )}
      </div>
      <div className="px-4 pt-2 pb-[max(1rem,env(safe-area-inset-bottom))] max-sm:px-2">
        {blocked && <p className="px-2 pb-2 text-xs text-warn">{blocked}</p>}
        {recorder.error && (
          <p role="alert" className="px-2 pb-2 text-xs text-destructive">
            {recorder.error}
            <button type="button" onClick={recorder.clearError} className="ml-2 text-muted-foreground underline">
              知道了
            </button>
          </p>
        )}
        {staged.length > 0 && (
          <ul aria-label="要发的图片" className="mb-2 flex flex-wrap gap-2 px-1">
            {staged.map((s) => (
              <li key={s.key} className="relative">
                <img src={s.thumb} alt={s.file.name} title={s.file.name} className="size-16 rounded-xl border object-cover" />
                <button
                  type="button"
                  onClick={() => setStaged((list) => list.filter((x) => x.key !== s.key))}
                  aria-label={`不发 ${s.file.name}`}
                  className="absolute -top-1.5 -right-1.5 grid size-5 place-items-center rounded-full bg-foreground text-background shadow"
                >
                  <X className="size-3" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="relative flex items-end gap-1 rounded-[26px] border bg-card p-1.5 shadow-sm focus-within:ring-2 focus-within:ring-ring">
          <button
            type="button"
            onClick={() => setPicking((v) => !v)}
            disabled={Boolean(blocked)}
            aria-label="发表情包"
            aria-expanded={picking}
            title="表情包"
            className={cn(
              'grid size-10 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-primary disabled:opacity-40',
              picking && 'bg-muted text-primary',
            )}
          >
            <Smile className="size-5" />
          </button>
          {picking && (
            <StickerPicker
              botId={bot.bot_id}
              botName={bot.name}
              onPick={sendSticker}
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
            disabled={Boolean(blocked) || sending}
            aria-label="放图片"
            title="放图片（也可以粘贴，或者拖进来），和话一起发"
            className="grid size-10 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-primary disabled:opacity-40"
          >
            <ImagePlus className="size-5" />
          </button>
          <input
            ref={picker}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            multiple
            hidden
            onChange={(e) => {
              const files = [...(e.target.files ?? [])]
              e.target.value = ''
              void takeFiles(files, false)
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
            placeholder={`跟${bot.name}说点什么…`}
            title="Enter 发送，Shift+Enter 换行"
            aria-label={`发给${bot.name}的话`}
            className="min-h-10 min-w-0 flex-1 resize-none bg-transparent py-2 text-base outline-none placeholder:text-muted-foreground sm:text-[15px]"
          />
          {listenReady && (
            <button
              type="button"
              onClick={() => void (recorder.state === 'recording' ? recorder.stop() : recorder.start())}
              disabled={recorder.state === 'busy' || Boolean(blocked)}
              aria-label={recorder.state === 'recording' ? '说完了' : '说话'}
              title={
                !canRecord()
                  ? '这里录不了音：手机要用 https 地址打开'
                  : recorder.state === 'recording'
                    ? '说完了，点一下变成文字'
                    : '点一下开始说话，说完再点一下'
              }
              className={cn(
                'flex h-10 shrink-0 items-center justify-center gap-1 rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-primary disabled:opacity-40',
                recorder.state === 'recording' ? 'bg-destructive/10 px-3 text-destructive' : 'w-10',
              )}
            >
              {recorder.state === 'busy' ? (
                <LoaderCircle className="size-5 animate-spin" />
              ) : recorder.state === 'recording' ? (
                <>
                  <span className="size-2 animate-pulse rounded-full bg-destructive" />
                  <span className="text-xs tabular-nums">{recorder.seconds}″</span>
                </>
              ) : (
                <Mic className="size-5" />
              )}
            </button>
          )}
          <button
            type="button"
            onClick={() => void send()}
            disabled={(!draft.trim() && !staged.length) || sending || Boolean(blocked)}
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

function NoChat({ bot, onOpened }: { bot: DeskBot; onOpened: () => void }) {
  const [state, setState] = useState<'idle' | 'busy' | string>('idle')
  const open = async () => {
    setState('busy')
    try {
      await sendJson('POST', `/api/desk/${encodeURIComponent(bot.bot_id)}/open`, {})
      setState('idle')
      onOpened()
    } catch (e) {
      setState(`没开成：${(e as Error).message}`)
    }
  }
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center text-sm">
      <BotGlyph name={bot.name} id={bot.bot_id} size="lg" />
      <p className="max-w-sm text-muted-foreground">
        {bot.name} 还没有私聊。可以在这里开一段只在桌面上的聊天；想和平台上的私聊连成一段，就先在 Bot 页的「私聊」里加上你自己。
      </p>
      <button
        type="button"
        disabled={state === 'busy'}
        onClick={() => void open()}
        className="rounded-full bg-primary px-5 py-2 font-medium text-primary-foreground active:scale-[0.98] disabled:opacity-50"
      >
        开始聊天
      </button>
      {state !== 'idle' && state !== 'busy' && <p role="alert" className="text-xs text-destructive">{state}</p>}
    </div>
  )
}

type Pane =
  | 'chat'
  | 'keepsakes'
  | 'reminders'
  | 'tasks'
  | 'files'
  | 'new'
  | 'new-own'
  | 'settings'
  | 'stickers'
  | 'group'
  | 'group-new'
  | 'group-settings'
  | 'home'

/** 群聊 in the list: the desk groups, each with what was said last. */
function GroupList({
  groups,
  lasts,
  selected,
  onSelect,
  wide = false,
}: {
  wide?: boolean
  groups: DeskGroup[]
  lasts: ReadonlyMap<number, Last>
  selected: number | null
  onSelect: (id: number) => void
}) {
  if (!groups.length) return null
  return (
    <>
      <p className={cn('px-4 pt-3 pb-1 text-xs font-medium text-muted-foreground', wide ? 'block' : 'hidden md:block')}>
        群聊
      </p>
      <ul className="space-y-1 px-2 pb-2">
        {groups.map((group) => {
          const live = group.view !== null ? lasts.get(group.view) : undefined
          const shown: DeskGroup = live
            ? { ...group, last: { ...live, sender_name: live.sender_name ?? '' } }
            : group
          const current = selected === group.id
          return (
            <li key={group.id}>
              <button
                type="button"
                onClick={() => onSelect(group.id)}
                aria-current={current ? 'true' : undefined}
                title={group.name}
                className={cn(
                  'flex w-full items-center gap-3 rounded-2xl px-2.5 py-2 text-left transition-[background-color,box-shadow] duration-200 active:scale-[0.99]',
                  current ? 'bg-card shadow-sm ring-1 ring-border' : 'hover:bg-card/60',
                )}
              >
                <GroupGlyph group={group} />
                <span className={cn('min-w-0 flex-1', wide ? 'block' : 'hidden md:block')}>
                  <span className="flex items-baseline gap-2">
                    <span className="truncate text-[15px] font-semibold">{group.name}</span>
                    {shown.last && (
                      <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
                        {shortTime(shown.last.sent_at)}
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">{groupLine(shown)}</span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </>
  )
}

/** A companion's words while you are elsewhere: a Windows notification (the desktop app's chat window
 * only, so the console open beside it does not say it twice). */
function useNotifications(bots: DeskBot[] | null, onOpen: (botId: string) => void) {
  const subscribe = useSubscribe()
  const byChannel = useRef(new Map<number, DeskBot>())
  useEffect(() => {
    byChannel.current = new Map((bots ?? []).flatMap((b) => b.chats.map((c) => [c.channel_id, b] as const)))
  }, [bots])
  useEffect(() => {
    if (!chatView || !desktop?.notify) return
    return subscribe((item) => {
      // Her own lines (主动搭话, ids "app:c:") show on the pet, not as a notification.
      if (item.kind !== 'message' || !item.message.from_self || item.message.external_id.startsWith('app:c:')) return
      const bot = byChannel.current.get(item.message.channel_id)
      if (!bot || (document.visibilityState === 'visible' && document.hasFocus())) return
      void desktop?.notify?.({ title: bot.name, body: item.message.text || '（发来一条消息）', botId: bot.bot_id })
    })
  }, [subscribe])
  useEffect(() => desktop?.onOpenBot?.(onOpen), [onOpen])
}

export function Chat({ standalone = false }: { standalone?: boolean }) {
  useDropGuard()
  const { data: bots, error, reload } = useBots<DeskBot[]>('/api/desk')
  const { data: groups, reload: reloadGroups } = useBots<DeskGroup[]>('/api/desk/groups')
  const [selected, setSelected] = useState<string | null>(null)
  const [groupId, setGroupId] = useState<number | null>(null)
  const group = groups?.find((g) => g.id === groupId) ?? null
  const companions = (bots ?? []).map((b) => ({ bot_id: b.bot_id, name: b.name }))
  const { botsVersion } = useLive()
  const life = useLiveData<LifeView>('/api/life', [botsVersion]).data
  const activityOf = (botId: string) => {
    const now = life?.on ? life.people.find((p) => p.bot_id === botId)?.now : null
    return now ? `在${now.room}${now.doing}${now.mood ? ` · ${now.mood}` : ''}` : ''
  }
  const roomOf = (botId: string) => (life?.on && life.people.find((p) => p.bot_id === botId)?.now?.room) || ''
  const [pane, setPane] = useState<Pane>('chat')
  // A phone shows the list or what is open, one at a time; the list first.
  const narrow = useNarrow()
  const [atList, setAtList] = useState(true)
  const phoneList = narrow && atList
  const open = useCallback((next: Pane) => {
    setPane(next)
    setAtList(false)
  }, [])
  // Leaving something opened from the list's own buttons: back to the list on a phone.
  const backToList = () => {
    setPane('chat')
    setAtList(true)
  }
  // The order just dragged into, shown until the list it was made from is replaced by the saved one.
  const [order, setOrder] = useState<{ ids: string[]; from: DeskBot[] | null } | null>(null)
  const [orderError, setOrderError] = useState('')
  const listed =
    order && bots && order.from === bots
      ? order.ids.map((id) => bots.find((b) => b.bot_id === id)).filter((b): b is DeskBot => !!b)
      : bots
  const bot = listed?.find((b) => b.bot_id === selected) ?? listed?.[0] ?? null
  const showBot = useCallback((botId: string) => {
    if (botId === '@home') return open('home') // the tray's 「打开小窝」
    setSelected(botId)
    setPane('chat')
    setAtList(false)
  }, [open])
  useNotifications(bots, showBot)
  const subscribe = useSubscribe()
  const [lasts, setLasts] = useState<ReadonlyMap<number, Last>>(new Map())
  useEffect(
    () =>
      subscribe((item) => {
        if (item.kind === 'recall') {
          // The one before it is the last again: from nest.
          const gone = item.message.channel_id
          setLasts((now) => new Map([...now].filter(([channel]) => channel !== gone)))
          reload()
          return
        }
        if (item.kind !== 'message') return
        const m = item.message
        const last = {
          text: m.text,
          from_self: m.from_self,
          sent_at: m.sent_at,
          sticker: Boolean(stickerOf(m.segments)),
          sender_name: m.sender_name,
        }
        setLasts((now) => new Map(now).set(m.channel_id, last))
      }),
    [subscribe, reload],
  )

  const reorder = async (ids: string[]) => {
    setOrder({ ids, from: bots })
    setOrderError('')
    try {
      await sendJson('PUT', '/api/desk/order', { bot_ids: ids })
      reload()
    } catch (e) {
      setOrderError(`顺序没保存：${(e as Error).message}`)
    }
  }

  let main
  if (pane === 'home') {
    main = (
      <Home onClose={backToList} onOpenChat={showBot} />
    )
  } else if (pane === 'group-new') {
    main = (
      <NewGroup
        companions={companions}
        onCancel={backToList}
        onDone={(made) => {
          reloadGroups()
          setGroupId(made.id)
          setPane('group')
        }}
      />
    )
  } else if (pane === 'group-settings' && group) {
    main = (
      <GroupSettings
        key={group.id}
        group={group}
        companions={companions}
        onClose={() => setPane('group')}
        onDissolved={() => {
          reloadGroups()
          setGroupId(null)
          backToList()
        }}
      />
    )
  } else if (pane === 'group' && group) {
    main = (
      <GroupConversation
        key={group.id}
        group={group}
        onSettings={() => setPane('group-settings')}
        onStickers={() => setPane('stickers')}
        onBack={narrow ? () => setAtList(true) : undefined}
      />
    )
  } else if (pane === 'new' || pane === 'new-own') {
    main = (
      <NewCompanion
        own={pane === 'new-own'}
        targets={(bots ?? []).map((b) => ({ bot_id: b.bot_id, name: b.name }))}
        onCancel={backToList}
        onDone={(id) => {
          showBot(id)
          reload()
        }}
      />
    )
  } else if (pane === 'settings') {
    main = <ChatSettings onClose={backToList} onHome={() => open('home')} />
  } else if (pane === 'stickers') {
    main = <StickerPacks initialBot={bot?.bot_id ?? null} onClose={backToList} />
  } else if (bots?.length === 0) {
    main = (
      <Welcome
        onDone={(id) => {
          showBot(id)
          reload()
        }}
        onNew={() => open('new-own')}
      />
    )
  } else if (bot === null) {
    main = <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">选一个伙伴开始聊天</div>
  } else if (pane === 'reminders') {
    const hasQQ = !bot.desk_only && bot.chats.some((c) => c.channel_id === bot.channel_id && !c.desk_only)
    main = (
      <Reminders key={bot.bot_id} botId={bot.bot_id} name={bot.name} hasQQ={hasQQ} onClose={() => setPane('chat')} />
    )
  } else if (pane === 'files') {
    main = <Files key={bot.bot_id} botId={bot.bot_id} name={bot.name} onClose={() => setPane('chat')} />
  } else if (pane === 'tasks') {
    main = <Tasks key={bot.bot_id} botId={bot.bot_id} name={bot.name} onClose={() => setPane('chat')} />
  } else if (pane === 'keepsakes') {
    main = <Keepsakes key={bot.bot_id} botId={bot.bot_id} name={bot.name} onClose={() => setPane('chat')} />
  } else if (bot.channel_id === null) {
    main = <NoChat key={bot.bot_id} bot={bot} onOpened={reload} />
  } else {
    main = (
      <Conversation
        key={`${bot.bot_id}:${bot.channel_id}`}
        bot={bot}
        channel={bot.channel_id}
        activity={activityOf(bot.bot_id)}
        room={roomOf(bot.bot_id)}
        onKeepsakes={() => setPane('keepsakes')}
        onReminders={() => setPane('reminders')}
        onTasks={() => setPane('tasks')}
        onFiles={() => setPane('files')}
        onStickers={() => setPane('stickers')}
        onBack={narrow ? () => setAtList(true) : undefined}
      />
    )
  }

  const footer = 'flex items-center gap-2.5 rounded-2xl px-3 py-2 text-left text-sm text-muted-foreground hover:bg-card/60 hover:text-foreground'
  return (
    <div
      className={cn(
        'flex overflow-hidden rounded-3xl border bg-card shadow-sm',
        standalone ? 'min-h-0 flex-1' : 'h-[calc(100svh-9rem)] min-h-96',
      )}
    >
      {/* A narrow window (the chat window can be 420 px) keeps the list to the avatars; a phone shows the
          list the whole width, and what is open the whole width after it. */}
      <aside
        className={cn(
          'flex shrink-0 flex-col border-r bg-muted/40',
          phoneList ? 'w-full border-r-0' : narrow ? 'hidden' : 'w-[4.75rem] md:w-60',
        )}
      >
        <div className="min-h-0 flex-1 overflow-y-auto">
          {error && <p className="p-4 text-sm text-destructive">读取失败：{error}</p>}
          {orderError && <p className="p-4 text-sm text-destructive">{orderError}</p>}
          {bots?.length === 0 && <p className="p-4 text-sm text-muted-foreground">还没有伙伴。点下面的按钮添一个。</p>}
          <BotList
            bots={listed ?? []}
            lasts={lasts}
            selected={['chat', 'keepsakes', 'reminders', 'tasks', 'files'].includes(pane) ? (bot?.bot_id ?? null) : null}
            onSelect={showBot}
            onReorder={(ids) => void reorder(ids)}
            wide={phoneList}
          />
          <GroupList
            groups={groups ?? []}
            lasts={lasts}
            selected={pane.startsWith('group') ? groupId : null}
            onSelect={(id) => {
              setGroupId(id)
              open('group')
            }}
            wide={phoneList}
          />
        </div>
        <div className="space-y-0.5 border-t p-2">
          <button
            type="button"
            onClick={() => open('new')}
            aria-pressed={pane === 'new'}
            title="新伙伴 / 搬一个进来"
            className={cn(footer, 'w-full', pane === 'new' && 'bg-card text-foreground')}
          >
            <UserPlus className="size-4 shrink-0" />
            <span className={phoneList ? 'inline' : 'hidden md:inline'}>新伙伴 / 搬一个进来</span>
          </button>
          <button
            type="button"
            onClick={() => open('home')}
            aria-pressed={pane === 'home'}
            title="小窝：她们的日子"
            className={cn(footer, 'w-full', pane === 'home' && 'bg-card text-foreground')}
          >
            <House className="size-4 shrink-0" />
            <span className={phoneList ? 'inline' : 'hidden md:inline'}>小窝</span>
          </button>
          <button
            type="button"
            onClick={() => open('group-new')}
            aria-pressed={pane === 'group-new'}
            title="建一个群"
            className={cn(footer, 'w-full', pane === 'group-new' && 'bg-card text-foreground')}
          >
            <Users className="size-4 shrink-0" />
            <span className={phoneList ? 'inline' : 'hidden md:inline'}>建群</span>
          </button>
          <button
            type="button"
            onClick={() => open('stickers')}
            aria-pressed={pane === 'stickers'}
            title="表情包管理"
            className={cn(footer, 'w-full', pane === 'stickers' && 'bg-card text-foreground')}
          >
            <Sticker className="size-4 shrink-0" />
            <span className={phoneList ? 'inline' : 'hidden md:inline'}>表情包</span>
          </button>
          <button
            type="button"
            onClick={() => open('settings')}
            aria-pressed={pane === 'settings'}
            title="设置：外观、头像、声音"
            className={cn(footer, 'w-full', pane === 'settings' && 'bg-card text-foreground')}
          >
            <MeGlyph size="sm" />
            <span className={phoneList ? 'inline' : 'hidden md:inline'}>设置</span>
          </button>
        </div>
      </aside>
      {/* Switching panels or companions: the new one fades in instead of snapping. */}
      <div
        key={pane === 'chat' ? `chat:${bot?.bot_id ?? ''}` : pane === 'group' ? `group:${groupId ?? ''}` : pane}
        className={cn(
          'flex min-h-0 min-w-0 flex-1 animate-in fade-in duration-200 motion-reduce:animate-none',
          phoneList && 'hidden',
        )}
      >
        {main}
      </div>
    </div>
  )
}
