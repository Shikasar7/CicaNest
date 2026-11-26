import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Lightbulb, LoaderCircle, SendHorizontal, Soup as SoupIcon, Sparkles } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { MeGlyph } from '@/components/chat/MyAvatar'
import { Segmented } from '@/components/Segmented'
import type { GameView, SoupFlavor, SoupQuestion } from '@/lib/api'
import { stickerImage } from '@/lib/sticker-packs'
import { cn } from '@/lib/utils'

/** 海龟汤: her puzzle on a card at the top, then your questions and her answers (是 / 不是 / 无关 / 是也不是, with
 * a word of her own and sometimes a 表情包), the hints she gave, and once it is over the 汤底. */

export const FLAVOR_NAME: Record<SoupFlavor, string> = { clear: '清汤', red: '红汤', funny: '搞笑' }
export const SOUP_LEVEL: Record<string, string> = { easy: '简单', normal: '普通', hard: '烧脑' }

const ANSWER_LOOK: Record<string, string> = {
  是: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200',
  不是: 'bg-rose-100 text-rose-800 dark:bg-rose-900/50 dark:text-rose-200',
  无关: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200',
  是也不是: 'bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200',
  猜中了: 'bg-primary text-primary-foreground',
  还差一点: 'bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200',
  不对哦: 'bg-rose-100 text-rose-800 dark:bg-rose-900/50 dark:text-rose-200',
}

function verdict(q: SoupQuestion): string | null {
  if (q.guess) return q.solved === null ? null : q.solved ? '猜中了' : q.close ? '还差一点' : '不对哦'
  return q.answer
}

function Dots() {
  return (
    <span className="flex gap-1 py-1" aria-label="在想">
      {[0, 1, 2].map((i) => (
        <span key={i} className="dot-pulse size-1.5 rounded-full bg-muted-foreground" style={{ animationDelay: `${i * 0.2}s` }} />
      ))}
    </span>
  )
}

function Exchange({ q, botId, name }: { q: SoupQuestion; botId: string; name: string }) {
  const said = verdict(q)
  return (
    <li className="space-y-1.5">
      <div className="flex flex-row-reverse items-start gap-2">
        <MeGlyph size="sm" />
        <p
          className={cn(
            'max-w-[78%] rounded-2xl rounded-tr-md px-3 py-1.5 text-sm leading-relaxed break-words shadow-sm animate-in fade-in slide-in-from-bottom-1 duration-200',
            q.guess ? 'border-2 border-primary/50 bg-card' : 'bg-primary text-primary-foreground',
          )}
        >
          {q.guess && <span className="mr-1.5 text-xs font-semibold text-primary">猜汤底</span>}
          {q.q}
        </p>
      </div>
      <div className="flex items-start gap-2">
        <BotGlyph name={name} id={botId} size="sm" />
        {said === null ? (
          <div className="rounded-2xl rounded-tl-md border bg-card px-3 py-1.5 shadow-sm">
            <Dots />
          </div>
        ) : (
          <div className="flex min-w-0 flex-col items-start gap-1 animate-in fade-in zoom-in-95 duration-200">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className={cn('rounded-full px-2.5 py-0.5 text-xs font-semibold', ANSWER_LOOK[said])}>{said}</span>
              {q.close && said !== '猜中了' && <span className="text-[11px] text-primary">很接近了！</span>}
            </div>
            {q.line && <p className="max-w-[85%] rounded-2xl rounded-tl-md border bg-card px-3 py-1.5 text-sm leading-relaxed shadow-sm">{q.line}</p>}
            {q.sticker_id !== undefined && (
              <img src={stickerImage(q.sticker_id)} alt={q.sticker_label ?? '表情'} className="max-h-24 max-w-24 rounded-xl object-contain" />
            )}
          </div>
        )}
      </div>
    </li>
  )
}

export function SoupBody({ game, botId, name }: { game: GameView; botId: string; name: string }) {
  const soup = game.soup
  const list = useRef<HTMLDivElement>(null)
  const count = soup?.questions.length ?? 0
  const pending = soup?.questions.some((q) => verdict(q) === null) ?? false
  useLayoutEffect(() => {
    const el = list.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [count, pending, game.status])
  if (!soup) return null
  const over = game.status !== 'playing'
  return (
    <div ref={list} className="min-h-0 w-full flex-1 overflow-y-auto px-3 pt-3 pb-4">
      <div className="mx-auto w-full max-w-[620px] space-y-4">
        <section className="relative overflow-hidden rounded-3xl border bg-[linear-gradient(160deg,color-mix(in_oklch,var(--primary)_8%,var(--card)),var(--card))] p-5 shadow-sm">
          <SoupIcon aria-hidden className="absolute -top-3 -right-3 size-24 text-primary/10" />
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <BotGlyph name={name} id={botId} size="sm" />
            <span>
              {name}出的题 · {FLAVOR_NAME[soup.flavor]} · {SOUP_LEVEL[game.level]}
            </span>
          </div>
          {soup.preparing ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" />
              {name}正在想一道题……
            </div>
          ) : (
            <>
              <h2 className="mt-3 text-xl font-semibold">{soup.title}</h2>
              <p className="mt-2 text-[15px] leading-relaxed whitespace-pre-wrap">{soup.surface}</p>
              <p className="mt-3 text-xs text-muted-foreground">
                只能问「是不是」的问题，她答是、不是、无关或是也不是。觉得知道了，就切到「猜汤底」说出来。
              </p>
            </>
          )}
        </section>
        {soup.hints.length > 0 && (
          <ul className="space-y-1.5">
            {soup.hints.map((h, i) => (
              <li key={i} className="flex items-start gap-2 rounded-2xl bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
                <Lightbulb className="mt-0.5 size-4 shrink-0" />
                <span>
                  <span className="font-medium">提示 {i + 1}：</span>
                  {h}
                </span>
              </li>
            ))}
          </ul>
        )}
        {count > 0 && (
          <ol className="space-y-3">
            {soup.questions.map((q, i) => (
              <Exchange key={`${i}:${q.at}`} q={q} botId={botId} name={name} />
            ))}
          </ol>
        )}
        {over && soup.bottom && (
          <section className="rounded-3xl border-2 border-primary/40 bg-card p-5 shadow-sm animate-in fade-in zoom-in-95 duration-300">
            <div className="flex items-center gap-2 font-semibold text-primary">
              <Sparkles className="size-4" />
              {game.status === 'won' ? '你猜中啦！汤底是——' : '汤底揭晓'}
            </div>
            <p className="mt-2 text-[15px] leading-relaxed whitespace-pre-wrap">{soup.bottom}</p>
            {soup.keys && soup.keys.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {soup.keys.map((k) => (
                  <span key={k} className="rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
                    {k}
                  </span>
                ))}
              </div>
            )}
            <p className="mt-3 text-xs text-muted-foreground">
              {name}出的题你猜中过 {game.record.won} 道，没猜出 {game.record.lost} 道
            </p>
          </section>
        )}
        {soup.error && !over && <p className="text-center text-xs text-destructive">{soup.error}</p>}
      </div>
    </div>
  )
}

/** Where you ask (or guess). */
export function SoupInput({
  game,
  name,
  onAsk,
}: {
  game: GameView
  name: string
  onAsk: (text: string, guess: boolean) => Promise<boolean>
}) {
  const [mode, setMode] = useState<'ask' | 'guess'>('ask')
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const ready = game.status === 'playing' && !game.thinking && !game.soup?.preparing
  const send = async () => {
    const text = draft.trim()
    if (!text || !ready || sending) return
    setSending(true)
    if (await onAsk(text, mode === 'guess')) {
      setDraft('')
      setMode('ask')
    }
    setSending(false)
  }
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void send()
    }
  }
  if (game.status !== 'playing') return null
  return (
    <div className="border-t bg-card/60 px-3 pt-2 pb-3 backdrop-blur max-sm:px-2">
      <div className="mb-2 flex items-center justify-between gap-2">
        <Segmented
          options={[
            { value: 'ask' as const, label: '问问题' },
            { value: 'guess' as const, label: '猜汤底' },
          ]}
          value={mode}
          onChange={setMode}
          label="问问题还是猜汤底"
        />
        <span className="truncate text-xs text-muted-foreground">问了 {game.soup?.questions.length ?? 0} 个</span>
      </div>
      <div className="flex items-center gap-1 rounded-full border bg-card p-1 shadow-sm focus-within:ring-2 focus-within:ring-ring">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          maxLength={200}
          disabled={!ready}
          placeholder={
            game.soup?.preparing
              ? `${name}还在想题目…`
              : game.thinking
                ? `${name}在想怎么答…`
                : mode === 'guess'
                  ? '说出你猜的真相'
                  : '问一个是不是的问题，比如「他认识那个人吗？」'
          }
          aria-label={mode === 'guess' ? '猜汤底' : '问问题'}
          className="h-9 min-w-0 flex-1 bg-transparent px-3 text-base outline-none placeholder:text-muted-foreground sm:text-sm"
        />
        <button
          type="button"
          onClick={() => void send()}
          disabled={!draft.trim() || !ready || sending}
          aria-label="发送"
          className="grid size-9 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground transition-[opacity,transform] active:scale-90 disabled:opacity-40"
        >
          <SendHorizontal className="size-4" />
        </button>
      </div>
    </div>
  )
}
