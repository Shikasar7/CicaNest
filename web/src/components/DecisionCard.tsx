import type { CSSProperties } from 'react'

import { BotGlyph } from '@/components/BotGlyph'
import type { Decision } from '@/lib/api'
import { formatTime } from '@/lib/format'
import { botLook } from '@/lib/persona'
import { cn } from '@/lib/utils'

const OUTCOME: Record<Decision['outcome'], string> = {
  would_reply: '本来会回',
  replied: '已发送',
  skipped: '想了想没回',
  suppressed: '没回',
  failed: '出错',
}

const STICKER: Record<NonNullable<Decision['sticker_state']>, string> = {
  sent: '发了这张表情',
  trial: '表情试运行：本来会发这张',
  failed: '这张表情没发出去',
  unknown: '这张表情不确定发出去没有',
}

/** A bot's decision in the group timeline, in the bot's own colour: solid edge when it spoke, dashed when it only
 *  would have, quiet when it held back. */
export function DecisionCard({ decision }: { decision: Decision }) {
  const speaks = decision.outcome === 'would_reply' || decision.outcome === 'replied'
  const failed = decision.outcome === 'failed'
  return (
    <li className="px-4 py-1.5">
      <div
        style={{ '--hue': botLook(decision.bot_name, decision.bot_id).hue } as CSSProperties}
        className={cn(
          'flex gap-3 rounded-xl border-l-[3px] px-3 py-2 text-sm',
          decision.outcome === 'replied' && 'border-l-[var(--hue)] bg-[color-mix(in_oklch,var(--hue)_8%,var(--card))]',
          decision.outcome === 'would_reply' &&
            'border-l-[color-mix(in_oklch,var(--hue)_55%,transparent)] border-dashed bg-[color-mix(in_oklch,var(--hue)_4%,var(--card))]',
          !speaks && !failed && 'border-l-border bg-muted/40',
          failed && 'border-l-destructive bg-destructive/5',
        )}
      >
        <BotGlyph name={decision.bot_name} id={decision.bot_id} size="sm" className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{decision.bot_name}</span>
            <span className={cn(failed && 'text-destructive')}>{OUTCOME[decision.outcome]}</span>
            <span>{decision.trigger === 'mention' ? '被点名' : decision.trigger === 'source' ? '看源码补答' : '主动插话'}</span>
            {decision.regenerated && <span>有新消息，重新想过一次</span>}
            <span className="ml-auto tabular-nums">{formatTime(decision.created_at)}</span>
          </div>
          {decision.trigger_text !== null && (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              回 {decision.trigger_sender}：{decision.trigger_text || '（非文本消息）'}
            </p>
          )}
          {speaks && decision.reply && <p className="mt-1 whitespace-pre-wrap break-words">{decision.reply}</p>}
          {decision.sticker_id && decision.sticker_state && (
            <div className="mt-1.5 flex items-end gap-2">
              <img
                src={`/api/stickers/${decision.sticker_id}/image`}
                alt="表情包"
                loading="lazy"
                className={cn(
                  'size-16 rounded-lg border bg-muted object-contain',
                  decision.sticker_state === 'trial' && 'border-dashed opacity-80',
                )}
              />
              <span className={cn('text-xs text-muted-foreground', decision.sticker_state === 'failed' && 'text-destructive')}>
                {STICKER[decision.sticker_state]}
              </span>
            </div>
          )}
          {speaks && decision.quote && (
            <p className="mt-1 text-xs text-muted-foreground">中间隔了好几条消息，发出时会引用原消息</p>
          )}
          {!speaks && <p className={cn('mt-1 text-xs', failed && 'text-destructive')}>{decision.reason}</p>}
          {decision.slang_terms && decision.slang_terms.length > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">参考了这个群的黑话：{decision.slang_terms.join('、')}</p>
          )}
        </div>
      </div>
    </li>
  )
}
