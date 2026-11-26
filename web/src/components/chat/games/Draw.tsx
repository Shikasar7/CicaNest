import { useLayoutEffect, useRef, useState } from 'react'
import { Check, Eraser, Lightbulb, Paintbrush, Pencil, SendHorizontal, Shuffle, Sparkles, Trash2, Undo2, X } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { sendBlob, sendJson, type DrawGuess, type GameView } from '@/lib/api'
import { stickerImage } from '@/lib/sticker-packs'
import { cn } from '@/lib/utils'

/** 你画我猜, either way round. You draw: the word on a card at the top (she does not know it), the sketch pad,
 * and her guesses — each with the drawing she saw, what she thinks it is, and a word of her own. She draws:
 * her picture (after a minute of drawing), your guesses, two hints, and at the end the word and her word. */

const UNITS = 512 // the pad's own size; what is sent is a 512 × 512 PNG
const INKS = ['#2f2a28', '#e5534b', '#f29b3d', '#f2cf3d', '#5cb36d', '#4c8fd6', '#9b6bd3', '#a0714f']
const SIZES = [5, 10, 20]
const PAPER = '#fffdf8'

type Stroke = { color: string; width: number; points: [number, number][] }
// What is on the pad of each game, while this window is open: going back to the chat and returning keeps it.
const pads = new Map<number, Stroke[]>()

function paint(ctx: CanvasRenderingContext2D, strokes: Stroke[], scale: number) {
  ctx.save()
  ctx.scale(scale, scale)
  ctx.fillStyle = PAPER
  ctx.fillRect(0, 0, UNITS, UNITS)
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  for (const s of strokes) {
    ctx.strokeStyle = s.color
    ctx.fillStyle = s.color
    ctx.lineWidth = s.width
    if (s.points.length === 1) {
      const [x, y] = s.points[0]
      ctx.beginPath()
      ctx.arc(x, y, s.width / 2, 0, Math.PI * 2)
      ctx.fill()
      continue
    }
    ctx.beginPath()
    ctx.moveTo(s.points[0][0], s.points[0][1])
    // Through the midpoints: smooth curves instead of corners.
    for (let i = 1; i < s.points.length - 1; i++) {
      const [x, y] = s.points[i]
      const [nx, ny] = s.points[i + 1]
      ctx.quadraticCurveTo(x, y, (x + nx) / 2, (y + ny) / 2)
    }
    const end = s.points[s.points.length - 1]
    ctx.lineTo(end[0], end[1])
    ctx.stroke()
  }
  ctx.restore()
}

function toPng(strokes: Stroke[]): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = UNITS
  canvas.height = UNITS
  const ctx = canvas.getContext('2d')
  if (ctx) paint(ctx, strokes, 1)
  return new Promise((done, fail) => canvas.toBlob((blob) => (blob ? done(blob) : fail(new Error('画没导出来'))), 'image/png'))
}

function Pad({
  strokes,
  onStrokes,
  disabled,
}: {
  strokes: Stroke[]
  /** Changes to the strokes, from the latest ones (two strokes ended close together both stay). */
  onStrokes: (change: (now: Stroke[]) => Stroke[]) => void
  disabled: boolean
}) {
  const box = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [side, setSide] = useState(0)
  const [ink, setInk] = useState(INKS[0])
  const [size, setSize] = useState(SIZES[1])
  const [erasing, setErasing] = useState(false)
  const live = useRef<Stroke | null>(null)

  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const measure = () => setSide(Math.floor(Math.min(el.clientWidth, 560)))
    measure()
    const watch = new ResizeObserver(measure)
    watch.observe(el)
    return () => watch.disconnect()
  }, [])
  useLayoutEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx || !side) return
    const ratio = window.devicePixelRatio || 1
    el.width = Math.round(side * ratio)
    el.height = Math.round(side * ratio)
    paint(ctx, live.current ? [...strokes, live.current] : strokes, (side * ratio) / UNITS)
  }, [strokes, side])

  const redraw = () => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (el && ctx) paint(ctx, live.current ? [...strokes, live.current] : strokes, el.width / UNITS)
  }
  const at = (e: React.PointerEvent<HTMLCanvasElement>): [number, number] => {
    const r = e.currentTarget.getBoundingClientRect()
    return [((e.clientX - r.left) / r.width) * UNITS, ((e.clientY - r.top) / r.height) * UNITS]
  }
  const width = erasing ? size * 2.2 : size

  return (
    <div className="w-full">
      <div ref={box} className="flex w-full justify-center">
        <div
          className="overflow-hidden rounded-[26px] shadow-[0_10px_30px_-14px_rgb(120_80_40/0.5)] ring-1 ring-black/5 dark:ring-white/10"
          style={{ width: side, height: side }}
        >
          <canvas
            ref={canvas}
            aria-label="画板"
            className={cn('size-full touch-none', disabled ? 'cursor-default' : erasing ? 'cursor-cell' : 'cursor-crosshair')}
            onPointerDown={(e) => {
              if (disabled || (e.pointerType === 'mouse' && e.button !== 0)) return
              e.currentTarget.setPointerCapture(e.pointerId)
              live.current = { color: erasing ? PAPER : ink, width, points: [at(e)] }
              redraw()
            }}
            onPointerMove={(e) => {
              if (!live.current) return
              const coalesced = e.nativeEvent.getCoalescedEvents?.() ?? []
              const events = coalesced.length ? coalesced : [e.nativeEvent]
              const r = e.currentTarget.getBoundingClientRect()
              for (const ev of events) {
                live.current.points.push([((ev.clientX - r.left) / r.width) * UNITS, ((ev.clientY - r.top) / r.height) * UNITS])
              }
              redraw()
            }}
            onPointerUp={() => {
              if (!live.current) return
              const done = live.current
              live.current = null
              onStrokes((now) => [...now, done])
            }}
            onPointerCancel={() => {
              live.current = null
              redraw()
            }}
          />
        </div>
      </div>
      <div className="mx-auto mt-3 flex max-w-[560px] flex-wrap items-center justify-center gap-x-3 gap-y-2">
        <div className="flex items-center gap-1.5" role="radiogroup" aria-label="颜色">
          {INKS.map((c) => (
            <button
              key={c}
              type="button"
              role="radio"
              aria-checked={!erasing && ink === c}
              aria-label={`颜色 ${c}`}
              disabled={disabled}
              onClick={() => {
                setInk(c)
                setErasing(false)
              }}
              className={cn(
                'size-6 rounded-full border-2 border-card shadow-sm transition-transform active:scale-90 disabled:opacity-40',
                !erasing && ink === c && 'scale-110 ring-2 ring-primary ring-offset-1 ring-offset-card',
              )}
              style={{ background: c }}
            />
          ))}
        </div>
        <div className="flex items-center gap-1 rounded-full bg-muted p-1" role="radiogroup" aria-label="粗细">
          {SIZES.map((w) => (
            <button
              key={w}
              type="button"
              role="radio"
              aria-checked={size === w}
              aria-label={`粗细 ${w}`}
              disabled={disabled}
              onClick={() => setSize(w)}
              className={cn('grid size-7 place-items-center rounded-full disabled:opacity-40', size === w && 'bg-card shadow-sm')}
            >
              <span className="rounded-full bg-foreground/70" style={{ width: Math.max(4, w / 1.6), height: Math.max(4, w / 1.6) }} />
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setErasing((v) => !v)}
            disabled={disabled}
            aria-pressed={erasing}
            title="橡皮"
            aria-label="橡皮"
            className={cn('grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted disabled:opacity-40', erasing && 'bg-primary/10 text-primary')}
          >
            <Eraser className="size-4" />
          </button>
          <button
            type="button"
            onClick={() => onStrokes((now) => now.slice(0, -1))}
            disabled={disabled || strokes.length === 0}
            title="撤销一笔"
            aria-label="撤销一笔"
            className="grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted disabled:opacity-40"
          >
            <Undo2 className="size-4" />
          </button>
          <button
            type="button"
            onClick={() => onStrokes(() => [])}
            disabled={disabled || strokes.length === 0}
            title="清空"
            aria-label="清空"
            className="grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted disabled:opacity-40"
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      </div>
    </div>
  )
}

function Guess({ g, gameId, botId, name }: { g: DrawGuess; gameId: number; botId: string; name: string }) {
  return (
    <li className="flex items-start gap-2.5">
      <img
        src={`/api/games/${gameId}/sketches/${g.n}`}
        alt={`第 ${g.n} 次给她看的画`}
        className="size-14 shrink-0 rounded-xl border bg-white object-contain shadow-sm"
      />
      <div className="flex min-w-0 items-start gap-2">
        <BotGlyph name={name} id={botId} size="sm" />
        {g.guess === null ? (
          <div className="rounded-2xl rounded-tl-md border bg-card px-3 py-2 text-sm text-muted-foreground shadow-sm">
            正在看你的画
            <span className="dot-pulse">…</span>
          </div>
        ) : (
          <div className="flex min-w-0 flex-col items-start gap-1 animate-in fade-in zoom-in-95 duration-200">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="rounded-2xl rounded-tl-md border bg-card px-3 py-1.5 text-sm font-medium shadow-sm">是……{g.guess}吗？</span>
              <span
                className={cn(
                  'inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-xs font-semibold',
                  g.right
                    ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200'
                    : 'bg-rose-100 text-rose-800 dark:bg-rose-900/50 dark:text-rose-200',
                )}
              >
                {g.right ? <Check className="size-3" /> : <X className="size-3" />}
                {g.right ? '猜中' : '不对'}
              </span>
            </div>
            {g.line && <p className="max-w-[90%] rounded-2xl rounded-tl-md border bg-card px-3 py-1.5 text-sm shadow-sm">{g.line}</p>}
            {g.sticker_id !== undefined && (
              <img src={stickerImage(g.sticker_id)} alt={g.sticker_label ?? '表情'} className="max-h-20 max-w-20 rounded-xl object-contain" />
            )}
          </div>
        )}
      </div>
    </li>
  )
}

function YouDraw({
  game,
  botId,
  name,
  onGame,
}: {
  game: GameView
  botId: string
  name: string
  /** A newer view of the game, from what was sent. */
  onGame: (game: GameView) => void
}) {
  const draw = game.draw
  const [drawn, setDrawn] = useState<{ id: number; strokes: Stroke[] }>(() => ({ id: game.id, strokes: pads.get(game.id) ?? [] }))
  const strokes = drawn.id === game.id ? drawn.strokes : []
  const [own, setOwn] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  if (!draw) return null
  const playing = game.status === 'playing'
  const started = draw.guesses.length > 0
  const word = async (body: { word?: string }) => {
    setError('')
    try {
      onGame(await sendJson<GameView>('POST', `/api/games/${game.id}/word`, { bot_id: botId, ...body }))
      setOwn(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const handOver = async () => {
    setSending(true)
    setError('')
    try {
      const png = await toPng(strokes)
      onGame(await sendBlob<GameView>(`/api/games/${game.id}/sketches?bot_id=${encodeURIComponent(botId)}`, png))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSending(false)
    }
  }
  const ended = {
    won: `${name}猜中啦！就是「${draw.word}」`,
    lost: `三次都没猜中，答案是「${draw.word}」`,
    resigned: `答案是「${draw.word}」`,
  }[game.status as 'won' | 'lost' | 'resigned']
  const latestFirst = [...draw.guesses].reverse()
  return (
    <div className="min-h-0 w-full flex-1 overflow-y-auto px-3 pt-3 pb-4">
      <div className="mx-auto grid w-full max-w-[920px] gap-4 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="min-w-0 space-y-3">
          <section className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-3xl border bg-[linear-gradient(160deg,color-mix(in_oklch,var(--primary)_8%,var(--card)),var(--card))] px-5 py-3 shadow-sm">
            <div className="min-w-0 flex-1">
              <div className="text-xs text-muted-foreground">你要画（{name}不知道）</div>
              {own !== null ? (
                <form
                  className="mt-1 flex items-center gap-1.5"
                  onSubmit={(e) => {
                    e.preventDefault()
                    if (own.trim()) void word({ word: own.trim() })
                  }}
                >
                  <input
                    autoFocus
                    value={own}
                    onChange={(e) => setOwn(e.target.value)}
                    maxLength={12}
                    placeholder="写一个你想画的词"
                    aria-label="自己出的词"
                    className="h-9 min-w-0 flex-1 rounded-full border bg-card px-3 text-base outline-none focus:ring-2 focus:ring-ring sm:text-sm"
                  />
                  <button type="submit" disabled={!own.trim()} className="h-9 rounded-full bg-primary px-4 text-sm text-primary-foreground disabled:opacity-40">
                    用这个
                  </button>
                  <button type="button" onClick={() => setOwn(null)} className="h-9 rounded-full px-3 text-sm text-muted-foreground hover:bg-muted">
                    取消
                  </button>
                </form>
              ) : (
                <div className="text-2xl font-semibold tracking-wide">{draw.word}</div>
              )}
            </div>
            {playing && !started && own === null && (
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={() => void word({})}
                  className="inline-flex h-8 items-center gap-1 rounded-full border bg-card px-3 text-xs text-muted-foreground hover:text-foreground"
                >
                  <Shuffle className="size-3.5" />
                  换一个
                </button>
                <button
                  type="button"
                  onClick={() => setOwn('')}
                  className="inline-flex h-8 items-center gap-1 rounded-full border bg-card px-3 text-xs text-muted-foreground hover:text-foreground"
                >
                  <Pencil className="size-3.5" />
                  自己出
                </button>
              </div>
            )}
            {playing && <p className="w-full text-[11px] text-muted-foreground">别写字哦～画好了交给她，她最多猜 3 次，没猜中可以接着画。</p>}
          </section>
          {latestFirst[0] && (
            // A narrow window: her newest guess here, where it is seen without scrolling.
            <ol className="lg:hidden">
              <Guess g={latestFirst[0]} gameId={game.id} botId={botId} name={name} />
            </ol>
          )}
          {!playing && strokes.length === 0 && draw.guesses.length > 0 ? (
            <div className="flex justify-center">
              <img
                src={`/api/games/${game.id}/sketches/${draw.guesses[draw.guesses.length - 1].n}`}
                alt="最后交给她的画"
                className="aspect-square w-full max-w-[560px] rounded-[26px] bg-white object-contain shadow-[0_10px_30px_-14px_rgb(120_80_40/0.5)] ring-1 ring-black/5"
              />
            </div>
          ) : (
          <Pad
            strokes={strokes}
            onStrokes={(change) =>
              setDrawn((now) => {
                const next = change(now.id === game.id ? now.strokes : [])
                pads.set(game.id, next)
                return { id: game.id, strokes: next }
              })
            }
            disabled={!playing}
          />
          )}
          {playing && (
            <div className="flex flex-col items-center gap-1.5">
              <button
                type="button"
                onClick={() => void handOver()}
                disabled={sending || game.thinking || strokes.length === 0}
                className="inline-flex h-10 items-center gap-1.5 rounded-full bg-primary px-6 text-sm font-medium text-primary-foreground shadow-sm transition-transform active:scale-95 disabled:opacity-50"
              >
                <Sparkles className="size-4" />
                {game.thinking ? `${name}在看…` : started ? `再让${name}猜一次` : `画好了，让${name}猜`}
              </button>
              <span className="text-xs text-muted-foreground">还能猜 {draw.tries_left} 次</span>
            </div>
          )}
          {(error || draw.error) && <p className="text-center text-xs text-destructive">{error || draw.error}</p>}
        </div>
        <aside className="min-w-0 space-y-3">
          {ended && (
            <div className="rounded-3xl border-2 border-primary/40 bg-card p-4 text-center shadow-sm animate-in fade-in zoom-in-95 duration-300">
              <Sparkles className="mx-auto size-6 text-primary" />
              <div className="mt-1 font-semibold">{ended}</div>
              <div className="mt-2 text-xs text-muted-foreground">
                你画的{name}猜中过 {game.record.won} 幅，没猜出 {game.record.lost} 幅
              </div>
            </div>
          )}
          {latestFirst.length > 0 ? (
            <ol className="space-y-3">
              {latestFirst.map((g) => (
                <Guess key={g.n} g={g} gameId={game.id} botId={botId} name={name} />
              ))}
            </ol>
          ) : (
            <p className="rounded-2xl border border-dashed p-4 text-center text-xs text-muted-foreground">她猜的会出现在这里</p>
          )}
        </aside>
      </div>
    </div>
  )
}

function HerDraw({
  game,
  botId,
  name,
  onGame,
}: {
  game: GameView
  botId: string
  name: string
  onGame: (game: GameView) => void
}) {
  const draw = game.draw
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  if (!draw) return null
  const playing = game.status === 'playing'
  const guess = async () => {
    const text = draft.trim()
    if (!text || sending) return
    setSending(true)
    setError('')
    try {
      onGame(await sendJson<GameView>('POST', `/api/games/${game.id}/answer`, { bot_id: botId, text }))
      setDraft('')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSending(false)
    }
  }
  const ended = {
    won: `猜中啦！就是「${draw.word}」`,
    lost: `五次都没猜中，答案是「${draw.word}」`,
    resigned: `答案是「${draw.word}」`,
  }[game.status as 'won' | 'lost' | 'resigned']
  return (
    <div className="min-h-0 w-full flex-1 overflow-y-auto px-3 pt-3 pb-4">
      <div className="mx-auto grid w-full max-w-[920px] gap-4 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="min-w-0 space-y-3">
          <div className="flex justify-center">
            {draw.painted ? (
              <img
                src={`/api/games/${game.id}/sketches/0`}
                alt={`${name}画的画`}
                className="aspect-square w-full max-w-[560px] rounded-[26px] bg-white object-contain shadow-[0_10px_30px_-14px_rgb(120_80_40/0.5)] ring-1 ring-black/5 animate-in fade-in zoom-in-95 duration-500"
              />
            ) : (
              <div className="grid aspect-square w-full max-w-[560px] place-items-center rounded-[26px] bg-[#fffdf8] shadow-[0_10px_30px_-14px_rgb(120_80_40/0.5)] ring-1 ring-black/5 dark:bg-card">
                <div className="text-center">
                  <Paintbrush className="mx-auto size-10 animate-bounce text-primary" />
                  <div className="mt-3 font-medium">{name}正在画……</div>
                  <div className="mt-1 text-xs text-muted-foreground">大约一分钟，画好就出现在这里</div>
                </div>
              </div>
            )}
          </div>
          {playing && draw.painted && (
            <form
              className="mx-auto flex max-w-[560px] items-center gap-1 rounded-full border bg-card p-1 shadow-sm focus-within:ring-2 focus-within:ring-ring"
              onSubmit={(e) => {
                e.preventDefault()
                void guess()
              }}
            >
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={20}
                placeholder={`她画的是什么？还能猜 ${draw.tries_left} 次`}
                aria-label="你猜的词"
                className="h-9 min-w-0 flex-1 bg-transparent px-3 text-base outline-none placeholder:text-muted-foreground sm:text-sm"
              />
              <button
                type="submit"
                disabled={!draft.trim() || sending}
                aria-label="猜"
                className="grid size-9 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground transition-[opacity,transform] active:scale-90 disabled:opacity-40"
              >
                <SendHorizontal className="size-4" />
              </button>
            </form>
          )}
          {draw.hints.length > 0 && (
            <div className="mx-auto flex max-w-[560px] flex-wrap justify-center gap-1.5">
              {draw.hints.map((h) => (
                <span key={h} className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-3 py-1 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
                  <Lightbulb className="size-3.5" />
                  {h}
                </span>
              ))}
            </div>
          )}
          {(error || draw.error) && <p className="text-center text-xs text-destructive">{error || draw.error}</p>}
        </div>
        <aside className="min-w-0 space-y-3">
          {ended && (
            <div className="rounded-3xl border-2 border-primary/40 bg-card p-4 text-center shadow-sm animate-in fade-in zoom-in-95 duration-300">
              <Sparkles className="mx-auto size-6 text-primary" />
              <div className="mt-1 font-semibold">{ended}</div>
              {(draw.line || draw.line_sticker) && (
                <div className="mt-3 flex items-start justify-center gap-2 text-left">
                  <BotGlyph name={name} id={botId} size="sm" />
                  <div className="flex flex-col items-start gap-1">
                    {draw.line && <p className="rounded-2xl rounded-tl-md border bg-card px-3 py-1.5 text-sm shadow-sm">{draw.line}</p>}
                    {draw.line_sticker && (
                      <img src={stickerImage(draw.line_sticker.id)} alt={draw.line_sticker.label} className="max-h-20 max-w-20 rounded-xl object-contain" />
                    )}
                  </div>
                </div>
              )}
              <div className="mt-2 text-xs text-muted-foreground">
                你画我猜一共猜中过 {game.record.won} 幅，没猜出 {game.record.lost} 幅
              </div>
            </div>
          )}
          {draw.guesses.length > 0 ? (
            <ol className="flex flex-wrap gap-1.5">
              {draw.guesses.map((g) => (
                <li
                  key={g.n}
                  className={cn(
                    'inline-flex items-center gap-1 rounded-full px-3 py-1 text-sm animate-in fade-in zoom-in-95 duration-200',
                    g.right
                      ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200'
                      : 'bg-muted text-muted-foreground line-through decoration-rose-400',
                  )}
                >
                  {g.right ? <Check className="size-3.5" /> : <X className="size-3.5 text-rose-500" />}
                  {g.guess}
                </li>
              ))}
            </ol>
          ) : playing ? (
            <p className="rounded-2xl border border-dashed p-4 text-center text-xs text-muted-foreground">
              {draw.painted ? '看出来是什么了吗？在下面写你猜的词' : `等${name}画完就可以猜了`}
            </p>
          ) : null}
        </aside>
      </div>
    </div>
  )
}

export function DrawBody(props: { game: GameView; botId: string; name: string; onGame: (game: GameView) => void }) {
  return props.game.draw?.mode === 'her' ? <HerDraw {...props} /> : <YouDraw {...props} />
}
