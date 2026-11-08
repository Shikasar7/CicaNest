import { cn } from '@/lib/utils'

/** The nest icon, the same picture as the desktop shortcut and the browser tab. */
export function NestMark({ className }: { className?: string }) {
  return <img src="/icon.png" alt="" aria-hidden className={cn('size-7', className)} />
}
