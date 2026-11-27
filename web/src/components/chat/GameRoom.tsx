import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Brush, ChevronLeft, Eye, Flag, Gamepad2, Handshake, Lightbulb, LoaderCircle, RotateCcw, SendHorizontal, Smile, Soup, Sparkles, Trophy, Undo2 } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { MeGlyph } from '@/components/chat/MyAvatar'
import { GomokuBoard, ReversiBoard } from '@/components/chat/games/boards'
import { DrawBody } from '@/components/chat/games/Draw'
import { FLAVOR_NAME, SoupBody, SoupInput } from '@/components/chat/games/Soup'
import { StickerPicker } from '@/components/chat/StickerPicker'
import { Segmented } from '@/components/Segmented'
import { getJson, sendJson, type GameKind, type GameLevel, type GameView, type Message, type SoupFlavor } from '@/lib/api'
import { inviteOf, useGame } from '@/lib/games'
import { useSubscribe } from '@/lib/live'
import { stickerImage, stickerOf } from '@/lib/sticker-packs'
import { cn } from '@/lib/utils'

/** 小游戏 with a companion: 五子棋 and 黑白棋 on a board, and 海龟汤. On a board game the board is in the middle,
 * the two of you above it with what each just said popping up as a bubble, and her chat (the same chat as
 * the conversation) to the right when there is room. Her moves come by themselves after a moment; what she
 * says comes as messages of the chat. 海龟汤 has its own room (components/chat/games/Soup). */

const LEVELS: { value: GameLevel; label: string }[] = [
  { value: 'easy', label: '轻松' },
  { value: 'normal', label: '认真' },
  { value: 'hard', label: '全力' },
]
const LEVEL_NAME: Record<GameLevel, string> = { easy: '轻松', normal: '认真', hard: '全力' }
type First = '' | 'you' | 'her'
const GAME_NAME: Record<GameKind, string> = { gomoku: '五子棋', reversi: '黑白棋', soup: '海龟汤', draw: '你画我猜' }
const PLAY_WORD: Record<GameKind, string> = { gomoku: '下五子棋', reversi: '下黑白棋', soup: '玩海龟汤', draw: '玩你画我猜' }
const GAMES: { kind: GameKind; Icon: typeof Gamepad2 }[] = [
  { kind: 'gomoku', Icon: Gamepad2 },
  { kind: 'reversi', Icon: Sparkles },
  { kind: 'soup', Icon: Soup },
  { kind: 'draw', Icon: Brush },
]
const DRAW_LEVELS: { value: GameLevel; label: string }[] = [
  { value: 'easy', label: '简单' },
  { value: 'normal', label: '普通' },
  { value: 'hard', label: '有点难' },
]
const SOUP_LEVELS: { value: GameLevel; label: string }[] = [
  { value: 'easy', label: '简单' },
  { value: 'normal', label: '普通' },
  { value: 'hard', label: '烧脑' },
]
const QUICK = ['好棋！', '等等，让我想想', '嘿嘿～', '不许耍赖哦']
const SHOUT_MS = 9000 // a line stays over its speaker this long
const WAIT_MS = 60_000 // her answer to what you typed: the 「…」 gives up after this
/** The square that fits the space the board has: give `place` to that space as its ref. */
function useSide() {
  const [el, place] = useState<HTMLDivElement | null>(null)
  const [side, setSide] = useState(0)
  useLayoutEffect(() => {
    if (!el) return
    const measure = () => setSide(Math.max(200, Math.floor(Math.min(el.clientWidth, el.clientHeight, 620))))
    const watch = new ResizeObserver(measure)
    watch.observe(el)
    return () => watch.disconnect()
  }, [el])
  return { place, side: el ? side : 0 }
}

/** What one of you said last, over their head for a moment. */
function Shout({ message, hers }: { message: Message | 'typing'; hers: boolean }) {
  const sticker = message === 'typing' ? null : stickerOf(message.segments)
  return (
    <div
      className={cn(
        'shout-in absolute top-full z-20 mt-2 w-max max-w-[min(15rem,42vw)] rounded-2xl border bg-card px-3 py-2 text-sm leading-snug shadow-md',
        hers ? 'left-0 rounded-tl-md' : 'right-0 rounded-tr-md',
      )}
    >
      {message === 'typing' ? (
        <span className="flex gap-1 py-1" aria-label="在打字">
          {[0, 1, 2].map((i) => (
            <span key={i} className="dot-pulse size-1.5 rounded-full bg-muted-foreground" style={{ animationDelay: `${i * 0.2}s` }} />
          ))}
        </span>
      ) : sticker ? (
        <img src={stickerImage(sticker.id)} alt={sticker.label} className="max-h-20 max-w-20 rounded-xl object-contain" />
      ) : (
        <span className="line-clamp-3 break-words">{message.text}</span>
      )}
    </div>
  )
}

function Player({
  hers,
  botId,
  name,
  color,
  status,
  active,
  shout,
}: {
  hers: boolean
  botId: string
  name: string
  color: 1 | 2
  status: string
  active: boolean
  shout: Message | 'typing' | null
}) {
  return (
    <div className={cn('relative flex min-w-0 items-center gap-2.5', !hers && 'flex-row-reverse text-right')}>
      <div className={cn('relative shrink-0 rounded-full p-0.5 transition-shadow', active && 'shadow-[0_0_0_3px_var(--primary)]')}>
        {hers ? <BotGlyph name={name} id={botId} size="lg" /> : <MeGlyph size="lg" />}
        <span
          aria-hidden
          className={cn(
            'absolute -right-0.5 -bottom-0.5 size-5 rounded-full border-2 border-card shadow-sm',
            color === 1 ? 'bg-[radial-gradient(circle_at_35%_30%,#6b5a57,#191313)]' : 'bg-[radial-gradient(circle_at_35%_30%,#fff,#e4d7c2)]',
          )}
        />
      </div>
      <div className="min-w-0">
        <div className="truncate font-semibold">{hers ? name : '你'}</div>
        <div className={cn('truncate text-xs', active ? 'text-primary' : 'text-muted-foreground')}>{status}</div>
      </div>
      {shout && <Shout key={shout === 'typing' ? 'typing' : shout.id} message={shout} hers={hers} />}
    </div>
  )
}

function Result({ game, name, onAgain, onLook }: { game: GameView; name: string; onAgain: () => void; onLook: () => void }) {
  const yours = game.her_color === 1 ? '2' : '1'
  const hers = game.her_color === 1 ? '1' : '2'
  const count = game.score ? `你 ${game.score[yours]} 子，${name} ${game.score[hers]} 子` : ''
  const said = {
    won: { Icon: Trophy, title: '你赢啦！', line: count || `${name}正在嘟囔呢` },
    lost: { Icon: Sparkles, title: `${name}赢了～`, line: count || '再来一局扳回来？' },
    draw: { Icon: Handshake, title: '和棋！', line: count || '棋盘都下满了，不分上下' },
    resigned: { Icon: Flag, title: '你认输了', line: `${name}偷偷乐了一下` },
    left: { Icon: Flag, title: '这局没下完', line: '' },
    playing: { Icon: Sparkles, title: '', line: '' },
  }[game.status]
  const { won, lost, draw } = game.record
  return (
    <div className="absolute inset-0 z-10 grid place-items-center rounded-[28px] bg-card/55 p-4 backdrop-blur-[2px] animate-in fade-in duration-300">
      <div className="w-full max-w-64 rounded-3xl border bg-card p-5 text-center shadow-lg animate-in zoom-in-90 duration-300">
        <said.Icon className="mx-auto size-8 text-primary" />
        <div className="mt-2 text-xl font-semibold">{said.title}</div>
        {said.line && <div className="mt-1 text-sm text-muted-foreground">{said.line}</div>}
        <div className="mt-3 text-xs text-muted-foreground">
          你们下过 {won + lost + draw} 局：你赢 {won} 局，{name}赢 {lost} 局{draw ? `，和 ${draw} 局` : ''}
        </div>
        <div className="mt-4 flex justify-center gap-2">
          <button type="button" onClick={onLook} className="rounded-full border px-4 py-2 text-sm hover:bg-muted">
            看看棋盘
          </button>
          <button type="button" onClick={onAgain} className="rounded-full bg-primary px-4 py-2 text-sm text-primary-foreground active:scale-95">
            再来一局
          </button>
        </div>
      </div>
    </div>
  )
}

function Setup({
  botId,
  name,
  game,
  busy,
  error,
  onStart,
  onCancel,
}: {
  botId: string
  name: string
  game: GameView | null
  busy: boolean
  error: string
  onStart: (kind: GameKind, level: GameLevel, first: First, flavor: SoupFlavor, word: string, mode: 'you' | 'her') => void
  onCancel: (() => void) | null
}) {
  const [kind, setKind] = useState<GameKind>(game?.kind ?? 'gomoku')
  const [level, setLevel] = useState<GameLevel>(game?.level ?? 'normal')
  const [first, setFirst] = useState<First>('')
  const [flavor, setFlavor] = useState<SoupFlavor>(game?.soup?.flavor ?? 'clear')
  const [word, setWord] = useState('')
  const [mode, setMode] = useState<'you' | 'her'>(game?.draw?.mode ?? 'you')
  const same = game?.kind === kind ? game : null
  const record = same?.record
  const played = record ? record.won + record.lost + record.draw : 0
  const nextFirst = same && same.her_color === 2 ? `${name}先` : '你先'
  const failed = game?.status === 'left' ? game.soup?.error : ''
  return (
    <div className="w-full max-w-sm rounded-3xl border bg-card p-6 text-center shadow-sm">
      <BotGlyph name={name} id={botId} size="lg" className="mx-auto" />
      <div className="mt-3 text-lg font-semibold">和{name}一起玩</div>
      <div className="mt-4 grid grid-cols-4 gap-1.5" role="radiogroup" aria-label="玩什么">
        {GAMES.map(({ kind: k, Icon }) => (
          <button
            key={k}
            type="button"
            role="radio"
            aria-checked={kind === k}
            onClick={() => setKind(k)}
            className={cn(
              'flex flex-col items-center gap-1 rounded-2xl border px-1 py-3 text-[13px] whitespace-nowrap transition-colors active:scale-95',
              kind === k ? 'border-primary/60 bg-primary/10 text-foreground' : 'text-muted-foreground hover:bg-muted',
            )}
          >
            <Icon className={cn('size-5', kind === k && 'text-primary')} />
            {GAME_NAME[k]}
          </button>
        ))}
      </div>
      <p className="mt-3 text-sm text-muted-foreground">
        {kind === 'gomoku'
          ? '黑子先下，横、竖、斜连成五个就赢。下的时候可以随时跟她说话、发表情。'
          : kind === 'reversi'
            ? '黑子先下，落子要夹住对方的子，夹住的都翻成你的；没地方下就跳过，最后子多的赢。'
            : kind === 'soup'
              ? `${name}出一道离奇的小谜题，你只能问「是不是」的问题，猜出背后的真相。`
              : mode === 'her'
                ? `${name}来画，你来猜。答案她不告诉你，你有五次机会，还能要两个提示。`
                : `你来画，${name}来猜。题目只有你看得到，她最多猜三次。`}
      </p>
      {played > 0 && record && (
        <p className="mt-2 text-xs text-muted-foreground">
          {kind === 'soup'
            ? `她出的题你猜中过 ${record.won} 道，没猜出 ${record.lost} 道`
            : kind === 'draw'
              ? `你画的她猜中过 ${record.won} 幅，没猜出 ${record.lost} 幅`
            : `你们下过 ${played} 局：你赢 ${record.won} 局，${name}赢 ${record.lost} 局${record.draw ? `，和 ${record.draw} 局` : ''}`}
        </p>
      )}
      <div className="mt-5 space-y-3 text-left text-sm">
        {kind === 'draw' ? (
          <>
            <div>
              <div id="draw-mode" className="mb-1.5 text-xs text-muted-foreground">
                谁来画
              </div>
              <Segmented
                options={[
                  { value: 'you' as const, label: `你画${name}猜` },
                  { value: 'her' as const, label: `${name}画你猜` },
                ]}
                value={mode}
                onChange={setMode}
                labelledBy="draw-mode"
              />
              {mode === 'her' && (
                <p className="mt-1 text-[11px] text-muted-foreground">她画一幅要一分钟左右，和「画图」共用每天 10 幅的额度</p>
              )}
            </div>
            <div>
              <div id="draw-level" className="mb-1.5 text-xs text-muted-foreground">
                题目难度
              </div>
              <Segmented options={DRAW_LEVELS} value={level} onChange={setLevel} labelledBy="draw-level" />
            </div>
            {mode === 'you' && (
            <div>
              <label htmlFor="draw-word" className="mb-1.5 block text-xs text-muted-foreground">
                自己出题（不填就随机给你一个）
              </label>
              <input
                id="draw-word"
                value={word}
                onChange={(e) => setWord(e.target.value)}
                maxLength={12}
                placeholder="比如：猫猫睡觉"
                className="h-9 w-full rounded-full border bg-card px-3 text-base outline-none focus:ring-2 focus:ring-ring sm:text-sm"
              />
            </div>
            )}
          </>
        ) : kind === 'soup' ? (
          <>
            <div>
              <div id="soup-flavor" className="mb-1.5 text-xs text-muted-foreground">
                口味
              </div>
              <Segmented
                options={(['clear', 'red', 'funny'] as SoupFlavor[]).map((f) => ({ value: f, label: FLAVOR_NAME[f] }))}
                value={flavor}
                onChange={setFlavor}
                labelledBy="soup-flavor"
              />
              <p className="mt-1 text-[11px] text-muted-foreground">
                {flavor === 'clear'
                  ? '不吓人：温馨、脑洞、小误会'
                  : flavor === 'red'
                    ? '有悬疑和反转，可能有人死去（不写血腥）'
                    : '哭笑不得的离谱结局'}
              </p>
            </div>
            <div>
              <div id="soup-level" className="mb-1.5 text-xs text-muted-foreground">
                难度
              </div>
              <Segmented options={SOUP_LEVELS} value={level} onChange={setLevel} labelledBy="soup-level" />
            </div>
          </>
        ) : (
          <>
            <div>
              <div id="game-level" className="mb-1.5 text-xs text-muted-foreground">
                她下得多认真
              </div>
              <Segmented options={LEVELS} value={level} onChange={setLevel} labelledBy="game-level" />
            </div>
            <div>
              <div id="game-first" className="mb-1.5 text-xs text-muted-foreground">
                谁先下（先下的执黑）
              </div>
              <Segmented
                options={[
                  { value: '' as First, label: `轮流（这局${nextFirst}）` },
                  { value: 'you' as First, label: '你先' },
                  { value: 'her' as First, label: `${name}先` },
                ]}
                value={first}
                onChange={setFirst}
                labelledBy="game-first"
              />
            </div>
          </>
        )}
      </div>
      {game?.status === 'playing' && <p className="mt-3 text-xs text-muted-foreground">现在这局还没完，开新的一局它就不算了。</p>}
      {(error || failed) && <p className="mt-3 text-sm text-destructive">{error || failed}</p>}
      <div className="mt-5 flex justify-center gap-2">
        {onCancel && (
          <button type="button" onClick={onCancel} className="rounded-full border px-5 py-2 text-sm hover:bg-muted">
            回到这局
          </button>
        )}
        <button
          type="button"
          onClick={() => onStart(kind, level, first, flavor, mode === 'you' ? word.trim() : '', mode)}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-full bg-primary px-6 py-2 text-sm font-medium text-primary-foreground transition-transform active:scale-95 disabled:opacity-50"
        >
          {busy && <LoaderCircle className="size-4 animate-spin" />}
          {kind === 'soup' ? '请她出题' : kind === 'draw' ? (mode === 'her' ? '请她画' : '开始画') : '开始'}
        </button>
      </div>
    </div>
  )
}

/** A line of the chat beside the board (wide windows). */
function Line({ message, botId, name }: { message: Message; botId: string; name: string }) {
  const hers = message.from_self
  const sticker = stickerOf(message.segments)
  return (
    <li className={cn('flex gap-2', !hers && 'flex-row-reverse')}>
      {hers ? <BotGlyph name={name} id={botId} size="sm" /> : <MeGlyph size="sm" />}
      {sticker ? (
        <img src={stickerImage(sticker.id)} alt={sticker.label} loading="lazy" className="max-h-24 max-w-24 rounded-xl object-contain animate-in fade-in zoom-in-90 duration-200" />
      ) : (
        <p
          className={cn(
            'max-w-[78%] rounded-2xl px-3 py-1.5 text-sm leading-relaxed break-words whitespace-pre-wrap shadow-sm animate-in fade-in slide-in-from-bottom-1 duration-200',
            hers ? 'rounded-tl-md border bg-card' : 'rounded-tr-md bg-primary text-primary-foreground',
          )}
        >
          {message.text || '（一条消息）'}
        </p>
      )}
    </li>
  )
}

export function GameRoom({
  botId,
  name,
  channel,
  blocked,
  onClose,
  onStickers,
}: {
  botId: string
  name: string
  channel: number
  /** Why she cannot talk now ('' when she can): you can still play. */
  blocked: string
  onClose: () => void
  onStickers: () => void
}) {
  const subscribe = useSubscribe()
  const { game, loaded, take } = useGame(botId)
  const [setup, setSetup] = useState(false)
  const [lookAtBoard, setLookAtBoard] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [shouts, setShouts] = useState<{ her: Message | null; you: Message | null }>({ her: null, you: null })
  const [waiting, setWaiting] = useState(false)
  const [draft, setDraft] = useState('')
  const [picking, setPicking] = useState(false)
  const [giveUp, setGiveUp] = useState(false)
  const { place, side } = useSide()
  const log = useRef<HTMLUListElement>(null)

  useEffect(() => {
    let cancelled = false
    getJson<Message[]>(`/api/channels/${channel}/messages?limit=40`).then(
      (rows) => !cancelled && setMessages(rows),
      () => {},
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
        setMessages((current) =>
          current.some((m) => m.id === message.id) ? current.map((m) => (m.id === message.id ? message : m)) : [...current, message],
        )
        if (item.kind !== 'message' || message.recalled_at) return
        setShouts((now) => (message.from_self ? { ...now, her: message } : { ...now, you: message }))
        if (message.from_self) setWaiting(false)
      }),
    [subscribe, channel],
  )
  // A line over someone's head goes away after a while.
  useEffect(() => {
    if (!shouts.her && !shouts.you) return
    const timer = setTimeout(() => setShouts({ her: null, you: null }), SHOUT_MS)
    return () => clearTimeout(timer)
  }, [shouts])
  useEffect(() => {
    if (!waiting) return
    const timer = setTimeout(() => setWaiting(false), WAIT_MS)
    return () => clearTimeout(timer)
  }, [waiting])
  useEffect(() => {
    if (!giveUp) return
    const timer = setTimeout(() => setGiveUp(false), 3000)
    return () => clearTimeout(timer)
  }, [giveUp])

  const since = game ? game.created_at - 120 : Infinity
  // Her invitation cards stay in the chat: here they would only read as text.
  const shown = messages.filter((m) => m.sent_at >= since && !m.recalled_at && !inviteOf(m.segments))
  useLayoutEffect(() => {
    const el = log.current
    if (el) el.scrollTop = el.scrollHeight
  }, [shown.length])

  const act = async (path: string, body: object) => {
    setBusy(true)
    setError('')
    try {
      take(await sendJson<GameView>('POST', path, { bot_id: botId, ...body }))
      return true
    } catch (e) {
      setError((e as Error).message)
      return false
    } finally {
      setBusy(false)
    }
  }
  const start = async (kind: GameKind, level: GameLevel, first: First, flavor: SoupFlavor, word: string, mode: 'you' | 'her') => {
    if (await act('/api/games', { kind, level, first, flavor, word, mode })) {
      setSetup(false)
      setLookAtBoard(false)
    }
  }
  const say = async (body: { text?: string; sticker_id?: number }) => {
    if (blocked) return
    setError('')
    try {
      await sendJson('POST', `/api/desk/${encodeURIComponent(botId)}/messages`, body)
      setWaiting(true)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const send = () => {
    const text = draft.trim()
    if (!text) return
    setDraft('')
    void say({ text })
  }
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
      e.preventDefault()
      send()
    }
  }

  const ask = async (text: string, guess: boolean) => (game ? act(`/api/games/${game.id}/ask`, { text, guess }) : false)

  const playing = game?.status === 'playing'
  const showSetup = loaded && (setup || !game || game.status === 'left')
  const isSoup = game?.kind === 'soup'
  const isDraw = game?.kind === 'draw'
  const isBoard = !isSoup && !isDraw
  const yours: 1 | 2 = game?.her_color === 1 ? 2 : 1
  const moves = game?.moves ?? []
  const placed = moves.filter(([x]) => x >= 0).length
  const myMoves = moves.filter(([x], i) => x >= 0 && (i % 2 === 0 ? 1 : 2) === yours).length
  const canPlay = Boolean(playing && game?.turn === 'you' && !game.thinking && !busy)
  const count = (color: 1 | 2) => (game?.score ? ` · ${game.score[String(color)]} 子` : '')
  const herStatus = !game
    ? ''
    : playing
      ? game.thinking
        ? '在想…'
        : game.turn === 'her'
          ? '轮到她'
          : '等你落子'
      : game.status === 'lost' || game.status === 'resigned'
        ? '赢了！'
        : game.status === 'won'
          ? '输了'
          : ''
  const myStatus = !game ? '' : playing ? (game.turn === 'you' && !game.thinking ? '轮到你啦' : '') : game.status === 'won' ? '赢了！' : ''

  return (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="relative z-10 flex items-center gap-2 border-b bg-card/70 px-4 py-2.5 backdrop-blur max-sm:px-2">
        <button
          type="button"
          onClick={onClose}
          aria-label="回到聊天"
          title="回到聊天"
          className="grid size-9 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-muted active:scale-90"
        >
          <ChevronLeft className="size-5" />
        </button>
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold">{showSetup || !game ? `和${name}一起玩` : `和${name}${PLAY_WORD[game.kind]}`}</div>
          {game && !showSetup && (
            <div className="truncate text-xs text-muted-foreground">
              {isDraw
                ? playing
                  ? game.draw?.mode === 'her'
                    ? game.draw.painting
                      ? `${name}在画`
                      : `你还能猜 ${game.draw.tries_left} 次`
                    : `她还能猜 ${game.draw?.tries_left ?? 0} 次`
                  : '这一幅画完了'
                : isSoup
                ? `${FLAVOR_NAME[game.soup?.flavor ?? 'clear']} · 问了 ${game.soup?.questions.length ?? 0} 个问题`
                : `${LEVEL_NAME[game.level]} · 第 ${placed} 手${game.undos ? ` · 悔了 ${game.undos} 步` : ''}`}
            </div>
          )}
        </div>
        {game && !showSetup && playing && isSoup && (
          <>
            <button
              type="button"
              onClick={() => void act(`/api/games/${game.id}/hint`, {})}
              disabled={busy || !game.soup?.hints_left || game.soup.preparing}
              title={`要一条提示（还有 ${game.soup?.hints_left ?? 0} 条）`}
              className="inline-flex h-9 items-center gap-1 rounded-full px-3 text-sm text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
            >
              <Lightbulb className="size-4" />
              <span className="max-sm:hidden">提示</span>
            </button>
            <button
              type="button"
              onClick={() => (giveUp ? void act(`/api/games/${game.id}/resign`, {}).then(() => setGiveUp(false)) : setGiveUp(true))}
              disabled={busy || game.soup?.preparing}
              title="不猜了，看汤底"
              className={cn(
                'inline-flex h-9 items-center gap-1 rounded-full px-3 text-sm hover:bg-muted disabled:opacity-40',
                giveUp ? 'bg-destructive/10 text-destructive' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              <Eye className="size-4" />
              <span className={cn(!giveUp && 'max-sm:hidden')}>{giveUp ? '再点一次揭晓' : '揭晓汤底'}</span>
            </button>
          </>
        )}
        {game && !showSetup && playing && isDraw && game.draw?.mode === 'her' && (
          <button
            type="button"
            onClick={() => void act(`/api/games/${game.id}/hint`, {})}
            disabled={busy || !game.draw.hints_left || !game.draw.painted}
            title={`要一个提示（还有 ${game.draw.hints_left} 个）`}
            className="inline-flex h-9 items-center gap-1 rounded-full px-3 text-sm text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
          >
            <Lightbulb className="size-4" />
            <span className="max-sm:hidden">提示</span>
          </button>
        )}
        {game && !showSetup && playing && isDraw && (
          <button
            type="button"
            onClick={() => (giveUp ? void act(`/api/games/${game.id}/resign`, {}).then(() => setGiveUp(false)) : setGiveUp(true))}
            disabled={busy || game.thinking}
            title={game.draw?.mode === 'her' ? '不猜了，看答案' : '不画了，直接告诉她答案'}
            className={cn(
              'inline-flex h-9 items-center gap-1 rounded-full px-3 text-sm hover:bg-muted disabled:opacity-40',
              giveUp ? 'bg-destructive/10 text-destructive' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Eye className="size-4" />
            <span className={cn(!giveUp && 'max-sm:hidden')}>
              {giveUp ? (game.draw?.mode === 'her' ? '再点一次看答案' : '再点一次公布') : game.draw?.mode === 'her' ? '看答案' : '公布答案'}
            </span>
          </button>
        )}
        {game && !showSetup && playing && isBoard && (
          <>
            <button
              type="button"
              onClick={() => void act(`/api/games/${game.id}/undo`, {})}
              disabled={busy || game.thinking || myMoves === 0}
              title="悔棋：收回你上一步（和她跟着下的那步）"
              className="inline-flex h-9 items-center gap-1 rounded-full px-3 text-sm text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
            >
              <Undo2 className="size-4" />
              <span className="max-sm:hidden">悔棋</span>
            </button>
            <button
              type="button"
              onClick={() => (giveUp ? void act(`/api/games/${game.id}/resign`, {}).then(() => setGiveUp(false)) : setGiveUp(true))}
              disabled={busy}
              title="认输"
              className={cn(
                'inline-flex h-9 items-center gap-1 rounded-full px-3 text-sm hover:bg-muted disabled:opacity-40',
                giveUp ? 'bg-destructive/10 text-destructive' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              <Flag className="size-4" />
              <span className={cn(!giveUp && 'max-sm:hidden')}>{giveUp ? '再点一次认输' : '认输'}</span>
            </button>
          </>
        )}
        {game && !showSetup && playing && (
          <button
            type="button"
            onClick={() => setSetup(true)}
            title="换个游戏（这局就不算了）"
            aria-label="换个游戏"
            className="grid size-9 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <Gamepad2 className="size-4" />
          </button>
        )}
        {game && !showSetup && !playing && (
          <button
            type="button"
            onClick={() => setSetup(true)}
            className="inline-flex h-9 items-center gap-1 rounded-full px-3 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <RotateCcw className="size-4" />
            {isSoup ? '再来一碗' : isDraw ? '再画一幅' : '再来一局'}
          </button>
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col items-center px-3 pt-3 pb-2">
          {showSetup || !game ? (
            <div className="grid w-full flex-1 place-items-center overflow-y-auto py-2">
              {loaded ? (
                <Setup
                  botId={botId}
                  name={name}
                  game={game}
                  busy={busy}
                  error={error}
                  onStart={(kind, level, first, flavor, word, mode) => void start(kind, level, first, flavor, word, mode)}
                  onCancel={game?.status === 'playing' ? () => setSetup(false) : null}
                />
              ) : (
                <LoaderCircle className="size-6 animate-spin text-muted-foreground" />
              )}
            </div>
          ) : isDraw ? (
            <DrawBody game={game} botId={botId} name={name} onGame={take} />
          ) : isSoup ? (
            <>
              <SoupBody game={game} botId={botId} name={name} />
              {error && <p className="pt-1 text-center text-xs text-destructive">{error}</p>}
            </>
          ) : (
            <>
              <div className="relative z-20 flex w-full max-w-[620px] items-start justify-between gap-3 px-1 pb-3">
                <Player
                  hers
                  botId={botId}
                  name={name}
                  color={game.her_color ?? 2}
                  status={herStatus + count(game.her_color ?? 2)}
                  active={playing && game.turn === 'her'}
                  shout={waiting && !shouts.her ? 'typing' : shouts.her}
                />
                <div className="shrink-0 self-center rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">VS</div>
                <Player
                  hers={false}
                  botId={botId}
                  name={name}
                  color={yours}
                  status={myStatus + count(yours)}
                  active={canPlay}
                  shout={shouts.you}
                />
              </div>
              <div ref={place} className="relative grid min-h-0 w-full flex-1 place-items-center">
                {side > 0 && (
                  <div className="relative">
                    {game.kind === 'reversi' ? (
                      <ReversiBoard game={game} side={side} canPlay={canPlay} onPlay={(x, y) => void act(`/api/games/${game.id}/moves`, { x, y })} />
                    ) : (
                      <GomokuBoard game={game} side={side} canPlay={canPlay} onPlay={(x, y) => void act(`/api/games/${game.id}/moves`, { x, y })} />
                    )}
                    {!playing && !lookAtBoard && (
                      <Result game={game} name={name} onAgain={() => setSetup(true)} onLook={() => setLookAtBoard(true)} />
                    )}
                  </div>
                )}
              </div>
              {error && <p className="pt-1 text-center text-xs text-destructive">{error}</p>}
            </>
          )}
        </div>
        <aside className="hidden w-72 shrink-0 flex-col border-l bg-muted/20 xl:flex">
          <div className="border-b px-4 py-2.5 text-xs text-muted-foreground">这局里说的话（也在聊天里）</div>
          <ul ref={log} className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-3 py-3">
            {shown.length === 0 && <li className="pt-6 text-center text-xs text-muted-foreground">边下边聊吧～</li>}
            {shown.map((m) => (
              <Line key={m.id} message={m} botId={botId} name={name} />
            ))}
          </ul>
        </aside>
      </div>

      {isDraw && !showSetup ? null : isSoup && !showSetup && game ? (
        <SoupInput game={game} name={name} onAsk={ask} />
      ) : (
      <div className="border-t bg-card/60 px-3 pt-2 pb-3 backdrop-blur max-sm:px-2">
        <div className="mb-2 flex gap-1.5 overflow-x-auto [scrollbar-width:none]">
          {QUICK.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => void say({ text: q })}
              disabled={Boolean(blocked)}
              className="shrink-0 rounded-full border bg-card px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground active:scale-95 disabled:opacity-40"
            >
              {q}
            </button>
          ))}
        </div>
        <div className="relative flex items-center gap-1 rounded-full border bg-card p-1 shadow-sm focus-within:ring-2 focus-within:ring-ring">
          <button
            type="button"
            onClick={() => setPicking((v) => !v)}
            disabled={Boolean(blocked)}
            aria-label="发表情包"
            aria-expanded={picking}
            title="表情包"
            className={cn(
              'grid size-9 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-primary disabled:opacity-40',
              picking && 'bg-muted text-primary',
            )}
          >
            <Smile className="size-5" />
          </button>
          {picking && (
            <StickerPicker
              botId={botId}
              botName={name}
              onPick={(id) => {
                setPicking(false)
                void say({ sticker_id: id })
              }}
              onManage={() => {
                setPicking(false)
                onStickers()
              }}
              onClose={() => setPicking(false)}
            />
          )}
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            maxLength={500}
            disabled={Boolean(blocked)}
            placeholder={blocked ? '她现在说不了话，棋还是可以下的' : `边下边跟${name}说…`}
            aria-label={`发给${name}的话`}
            className="h-9 min-w-0 flex-1 bg-transparent px-1 text-base outline-none placeholder:text-muted-foreground sm:text-sm"
          />
          <button
            type="button"
            onClick={send}
            disabled={!draft.trim() || Boolean(blocked)}
            aria-label="发送"
            className="grid size-9 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground transition-[opacity,transform] active:scale-90 disabled:opacity-40"
          >
            <SendHorizontal className="size-4" />
          </button>
        </div>
      </div>
      )}
    </div>
  )
}
