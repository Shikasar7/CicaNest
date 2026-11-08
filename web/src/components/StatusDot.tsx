import { cn } from '@/lib/utils'

export type Tone = 'ok' | 'wait' | 'bad' | 'off'

const TONE: Record<Tone, string> = {
  ok: 'bg-ok',
  wait: 'bg-warn',
  bad: 'bg-destructive',
  off: 'bg-muted-foreground/40',
}

/** A small state dot; waiting states pulse so the eye catches what is still changing. */
export function StatusDot({ tone, className }: { tone: Tone; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn('inline-block size-2 shrink-0 rounded-full transition-colors', TONE[tone], tone === 'wait' && 'dot-pulse', className)}
    />
  )
}
