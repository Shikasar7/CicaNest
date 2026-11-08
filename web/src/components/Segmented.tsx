import type { ComponentType } from 'react'

import { cn } from '@/lib/utils'

export type SegmentedOption<T extends string> = {
  value: T
  label: string
  Icon?: ComponentType<{ className?: string }>
  /** A state that really acts (正式发言, 推送): shown in the status green when chosen, the same for every bot. */
  accent?: boolean
}

/** The console's segmented control (系统 page log levels, a bot's mode): one radio group on a muted track. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  disabled = false,
  label,
  labelledBy,
  describedBy,
  iconOnly = false,
}: {
  options: SegmentedOption<T>[]
  value: T
  onChange: (next: T) => void
  disabled?: boolean
  /** aria-label, when there is no visible label to point at. */
  label?: string
  labelledBy?: string
  describedBy?: string
  /** Just the icons (each option's label becomes its name and tooltip), for tight spots like the sidebar. */
  iconOnly?: boolean
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      className="inline-flex w-fit flex-wrap rounded-lg bg-muted p-0.5"
    >
      {options.map((o) => {
        const current = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={current}
            aria-label={iconOnly ? o.label : undefined}
            title={iconOnly ? o.label : undefined}
            disabled={disabled}
            onClick={() => !current && onChange(o.value)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md py-1 text-sm transition-all duration-200 disabled:cursor-default',
              iconOnly ? 'px-2' : 'px-3',
              current
                ? cn('bg-card font-medium shadow-[var(--shadow-lift)]', o.accent ? 'text-ok' : 'text-foreground')
                : 'text-muted-foreground enabled:hover:text-foreground disabled:opacity-40',
            )}
          >
            {o.Icon && <o.Icon className="size-3.5" />}
            {!iconOnly && o.label}
          </button>
        )
      })}
    </div>
  )
}
