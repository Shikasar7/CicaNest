import { useState, type CSSProperties } from 'react'

import { useAvatarUrl } from '@/lib/live'
import { botLook } from '@/lib/persona'
import { cn } from '@/lib/utils'

const SIZES = {
  sm: 'size-6 text-[13px]',
  md: 'size-9 text-lg',
  lg: 'size-14 text-[28px]',
} as const

/** A bot's mark: its chosen picture, or its brush character on a disc of its colour. The character stays
 * underneath while a picture loads and comes back if it fails. Online bots breathe; offline ones go grey. */
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
  /** Show this picture (null: the brush character) instead of the one the bot has chosen. */
  picture?: string | null
  size?: keyof typeof SIZES
  online?: boolean
  breathing?: boolean
  className?: string
}) {
  const look = botLook(name, id)
  const chosen = useAvatarUrl(id)
  const picture = override === undefined ? chosen : override
  const [loaded, setLoaded] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const shown = picture !== null && picture !== failed && picture === loaded
  return (
    <span
      aria-hidden
      style={{ '--hue': look.hue } as CSSProperties}
      className={cn(
        'relative isolate inline-grid shrink-0 place-items-center rounded-full font-[family-name:var(--font-glyph)] leading-none transition-[background-color,color,filter] duration-500',
        'bg-[color-mix(in_oklch,var(--hue)_16%,var(--card))] text-[var(--hue)] ring-1 ring-[color-mix(in_oklch,var(--hue)_30%,transparent)]',
        !online && 'grayscale-[0.85] opacity-70',
        online && breathing && 'glyph-halo',
        SIZES[size],
        className,
      )}
    >
      <span className={cn('transition-opacity duration-300', shown && 'opacity-0')}>{look.glyph}</span>
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
