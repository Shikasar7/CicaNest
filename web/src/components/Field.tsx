import { useId, type ReactNode } from 'react'

import { Label } from '@/components/ui/label'
import { describedBy } from '@/lib/forms'
import { cn } from '@/lib/utils'

/** Props to spread on the control inside a Field. */
export type FieldControl = { id: string; 'aria-invalid': true | undefined; 'aria-describedby': string | undefined }

/**
 * A visibly labelled control with its hint, warning and inline error wired to it (`htmlFor`, `aria-invalid`,
 * `aria-describedby`). The control comes from the render prop: `{(control) => <Input {...control} />}`.
 */
export function Field({
  label,
  hint,
  warning,
  error,
  className,
  children,
}: {
  label: ReactNode
  hint?: ReactNode
  warning?: ReactNode
  error?: string
  className?: string
  children: (control: FieldControl) => ReactNode
}) {
  const id = useId()
  const hintId = `${id}-hint`
  const warningId = `${id}-warning`
  const errorId = `${id}-error`
  return (
    <div className={cn('grid content-start gap-1.5', className)}>
      <Label htmlFor={id}>{label}</Label>
      {children({
        id,
        'aria-invalid': error ? true : undefined,
        'aria-describedby': describedBy(error && errorId, warning ? warningId : null, hint ? hintId : null),
      })}
      {error && (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {warning && (
        <p id={warningId} className="text-xs text-amber-700 dark:text-amber-400">
          {warning}
        </p>
      )}
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  )
}
