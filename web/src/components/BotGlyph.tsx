import { useState, type CSSProperties } from 'react'

import { FaceGlyph } from '@/components/FaceGlyph'
import { useAvatarUrl } from '@/lib/live'
import { botLook } from '@/lib/persona'
import { cn } from '@/lib/utils'

const SIZES = {
  sm: 'size-6',
  md: 'size-9',
  /** Beside a message in the chat window. */
  chat: 'size-10',
  /** In the chat window's companion list. */
  list: 'size-12',
  lg: 'size-14',
} as const

/** The user's, until they give a picture of their own (components/chat/MyAvatar). */
const ME_PICTURE = '/me.webp'

/** A bot's mark: its chosen picture, or a little face on a disc of its colour. The face stays underneath while
 * a picture loads and comes back if it fails. Online bots breathe; offline ones go grey. */
export function BotGlyph({
  name,
  id,
  size = 'md',
  online = true,
  breathing = false,
  picture: override,
  className,
}: {
  name: string
  id?: string
  /** Show this picture (null: the little face) instead of the one the bot has chosen. */
  picture?: string | null
  size?: keyof typeof SIZES
  online?: boolean
  breathing?: boolean
  className?: string
}) {
  const look = botLook(name, id)
  const chosen = useAvatarUrl(id)
  const picture = override === undefined ? (chosen ?? (id === 'me' ? ME_PICTURE : null)) : override
  const [loaded, setLoaded] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const shown = picture !== null && picture !== failed && picture === loaded
  return (
    <span
      aria-hidden
      style={{ '--hue': look.hue } as CSSProperties}
      className={cn(
        'relative isolate inline-grid shrink-0 place-items-center rounded-full transition-[background-color,color,filter] duration-500',
        'bg-[color-mix(in_oklch,var(--hue)_16%,var(--card))] text-[var(--hue)] ring-1 ring-[color-mix(in_oklch,var(--hue)_30%,transparent)]',
        !online && 'grayscale-[0.85] opacity-70',
        online && breathing && 'glyph-halo',
        SIZES[size],
        className,
      )}
    >
      <FaceGlyph
        face={look.face}
        className={cn('size-[84%] transition-opacity duration-300', (shown || id === 'me') && 'opacity-0')}
      />
      {picture !== null && picture !== failed && (
        <img
          key={picture}
          src={picture}
          alt=""
          draggable={false}
          decoding="async"
          onLoad={() => setLoaded(picture)}
          onError={() => setFailed(picture)}
          className={cn(
            'absolute inset-0 size-full rounded-full object-cover transition-opacity duration-300',
            shown ? 'opacity-100' : 'opacity-0',
          )}
        />
      )}
    </span>
  )
}
