import { useId, type ComponentType, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'

import { useRememberedOpen } from '@/components/bots/shared'
import { cn } from '@/lib/utils'

/**
 * One part of a bot card (状态, 模型, 群, 私聊): a heading with its icon and a one-line summary, folding open
 * and closed. Folded, the content stays mounted (hidden), so a half-typed edit inside survives.
 */
export function Section({
  id,
  title,
  Icon,
  summary,
  defaultOpen = true,
  children,
}: {
  /** Where the open state is remembered: unique per bot and section. */
  id: string
  title: string
  Icon: ComponentType<{ className?: string }>
  /** What the section holds, shown beside the title (a count, a model name…). */
  summary?: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}) {
  const { open, toggle } = useRememberedOpen(`nest.bots.section.${id}`, defaultOpen)
  const bodyId = useId()
  return (
    <section className="border-t pt-4 first:border-t-0 first:pt-0">
      <h3>
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={bodyId}
          className="group/section flex w-full items-center gap-2.5 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover/section:text-foreground">
            <Icon className="size-4" />
          </span>
          <span className="text-sm font-semibold">{title}</span>
          {summary && <span className="min-w-0 truncate text-xs text-muted-foreground">{summary}</span>}
          <ChevronDown
            aria-hidden
            className={cn(
              'ml-auto size-4 shrink-0 text-muted-foreground transition-transform duration-200 motion-reduce:transition-none',
              !open && '-rotate-90',
            )}
          />
        </button>
      </h3>
      <div id={bodyId} hidden={!open} className="pt-3">
        {children}
      </div>
    </section>
  )
}
