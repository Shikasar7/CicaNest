import { useState } from 'react'

import { cn } from '@/lib/utils'

/** 撤回, under something you said: asks once more (it is gone for good, from her memory of the chat too). */
export function TakeBack({ always, onClick }: { always: boolean; onClick: () => Promise<void> }) {
  const [sure, setSure] = useState(false)
  const [busy, setBusy] = useState(false)
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => {
        if (!sure) return setSure(true)
        setBusy(true)
        void onClick().finally(() => {
          setBusy(false)
          setSure(false)
        })
      }}
      onBlur={() => setSure(false)}
      className={cn(
        'rounded-full px-2 text-[11px] text-muted-foreground transition-opacity hover:text-primary focus-visible:opacity-100 disabled:opacity-40',
        sure ? 'text-primary opacity-100' : always ? 'opacity-60 md:opacity-0 group-hover/bubble:opacity-100' : 'opacity-0 group-hover/bubble:opacity-100 max-md:hidden',
      )}
    >
      {sure ? '真的撤回？' : '撤回'}
    </button>
  )
}
