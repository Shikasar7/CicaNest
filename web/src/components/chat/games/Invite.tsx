import { useState } from 'react'
import { Brush, Gamepad2, LoaderCircle, Soup, Sparkles } from 'lucide-react'

import type { GameKind } from '@/lib/api'
import type { Invite } from '@/lib/games'
import { cn } from '@/lib/utils'

const ICONS: Record<GameKind, typeof Gamepad2> = { gomoku: Gamepad2, reversi: Sparkles, soup: Soup, draw: Brush }

function line(invite: Invite, name: string) {
  if (invite.kind === 'gomoku') return '横竖斜连成五个就赢'
  if (invite.kind === 'reversi') return '把她的棋子夹住，就翻成你的'
  if (invite.kind === 'soup') return `${name}出一道谜题，你来猜背后的真相`
  return invite.mode === 'her' ? `${name}来画，你来猜` : `你来画，${name}来猜`
}

export function InviteCard({
  invite,
  name,
  onPlay,
  fresh,
}: {
  invite: Invite
  /** Hers. */
  name: string
  /** Start it (or go back to it) and open the board; false when it did not start. */
  onPlay: (invite: Invite) => Promise<boolean>
  fresh: boolean
}) {
  const [starting, setStarting] = useState(false)
  const Icon = ICONS[invite.kind]
  return (
    <div
      className={cn(
        'w-64 max-w-full overflow-hidden rounded-[22px] rounded-tl-md border bg-card text-card-foreground shadow-sm',
        fresh && 'animate-in fade-in zoom-in-95 duration-200 ease-out motion-reduce:animate-none',
      )}
    >
      <div className="flex items-center gap-3 bg-gradient-to-br from-primary/15 via-primary/5 to-transparent px-4 pt-3.5 pb-3">
        <span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-card text-primary shadow-sm ring-1 ring-primary/15">
          <Icon className="size-5" />
        </span>
        <div className="min-w-0">
          <div className="text-[11px] text-muted-foreground">{name}邀请你一起玩</div>
          <div className="truncate text-base font-semibold">{invite.name}</div>
        </div>
      </div>
      <div className="px-4 pt-2 pb-3.5">
        <p className="text-xs text-muted-foreground">{line(invite, name)}</p>
        <button
          type="button"
          disabled={starting}
          onClick={async () => {
            setStarting(true)
            await onPlay(invite)
            setStarting(false)
          }}
          className="mt-2.5 inline-flex h-9 w-full items-center justify-center gap-1.5 rounded-full bg-primary text-sm font-medium text-primary-foreground shadow-sm transition-transform active:scale-95 disabled:opacity-60"
        >
          {starting ? <LoaderCircle className="size-4 animate-spin" /> : <Gamepad2 className="size-4" />}
          好呀，来玩
        </button>
      </div>
    </div>
  )
}
