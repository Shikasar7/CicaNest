import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** The outcome of an action, shown where it happened: a status for screen readers, an alert when it failed. */
export type Result = { ok: boolean; text: ReactNode }

export function ResultText({ result, className }: { result: Result | null; className?: string }) {
  if (!result) return null
  return (
    <p
      role={result.ok ? 'status' : 'alert'}
      className={cn('text-xs', result.ok ? 'text-muted-foreground' : 'text-destructive', className)}
    >
      {result.text}
    </p>
  )
}

/** Above data kept from an earlier fetch when the latest refetch failed. */
export function StaleNotice({ error }: { error: string }) {
  if (!error) return null
  return (
    <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      读取失败，下面是上次的数据：{error}
    </p>
  )
}

/** In place of a page while there is no data yet: loading, or why the first fetch failed. */
export function FirstLoad({ error, what }: { error: string; what: string }) {
  return error ? (
    <p role="alert" className="text-sm text-destructive">
      读取{what}失败：{error}
    </p>
  ) : (
    <p role="status" className="text-sm text-muted-foreground">
      读取中…
    </p>
  )
}
