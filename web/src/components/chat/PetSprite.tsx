import { useEffect, useState } from 'react'

import { desktop, type PetSprite as Sprite } from '@/lib/desktop'
import { cn } from '@/lib/utils'

/** Her desktop pet, small, in the 小窝 (the desktop app has the pictures; a browser shows her avatar instead). */

const known = new Map<string, Promise<Sprite | null>>()

/** Her pet, asked for once; null: she has none, or this is a browser. */
export function petSprite(botId: string): Promise<Sprite | null> {
  if (!desktop?.petSprite) return Promise.resolve(null)
  const asked = known.get(botId) ?? desktop.petSprite(botId).catch(() => null)
  known.set(botId, asked)
  return asked
}

export function usePetSprite(botId: string): Sprite | null {
  const [sprite, setSprite] = useState<Sprite | null>(null)
  useEffect(() => {
    let cancelled = false
    void petSprite(botId).then((found) => !cancelled && setSprite(found))
    return () => {
      cancelled = true
    }
  }, [botId])
  return sprite
}

/** One action of hers, playing. `height`: how tall she stands, in CSS pixels. */
export function PetSprite({ sprite, action, height, flip = false, className }: {
  sprite: Sprite
  action: string
  height: number
  flip?: boolean
  className?: string
}) {
  const shown = sprite.actions[action] ?? sprite.actions.idle
  const size = (height / sprite.figure) * sprite.frame
  const { src, frames, fps, loop } = shown
  const [at, setAt] = useState({ src, n: 0 })
  const frame = at.src === src ? at.n : 0 // another action starts at its first frame
  useEffect(() => {
    if (frames <= 1 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    let n = 0
    const timer = setInterval(() => {
      n += 1
      if (loop === false && n >= frames) return clearInterval(timer)
      setAt({ src, n: n % frames })
    }, 1000 / (fps || 6))
    return () => clearInterval(timer)
  }, [src, frames, fps, loop])
  return (
    <span
      aria-hidden
      className={cn('block shrink-0', className)}
      style={{
        width: size,
        height: size,
        // Her feet on the ground: what is under the baseline of her picture hangs below it.
        marginBottom: -((sprite.frame - sprite.baseline) / sprite.frame) * size,
        backgroundImage: `url("${src}")`,
        backgroundSize: `${size * frames}px ${size}px`,
        backgroundPosition: `${-frame * size}px 0`,
        backgroundRepeat: 'no-repeat',
        imageRendering: sprite.rendering === 'pixel' ? 'pixelated' : 'auto',
        transform: flip ? 'scaleX(-1)' : undefined,
      }}
    />
  )
}
