import type { ReactNode } from 'react'

import type { BotLook } from '@/lib/persona'

const EYE = '#2b3640'
const CHEEK = '#f7a6ab'

/** Ears and such, by the bot's id. */
const TOPS: Record<BotLook['face'], (fill: string, inner: string) => ReactNode> = {
  cat: (fill, inner) => (
    <>
      <path d="M11 22 L10.5 8.6 Q10.6 6.8 12.2 7.8 L22 14 Z M37 22 L37.5 8.6 Q37.4 6.8 35.8 7.8 L26 14 Z" fill={fill} />
      <path d="M13.2 17 L13 11.6 L17.6 14.6 Z M34.8 17 L35 11.6 L30.4 14.6 Z" fill={inner} />
    </>
  ),
  bear: (fill, inner) => (
    <>
      <circle cx="13" cy="13" r="6" fill={fill} />
      <circle cx="35" cy="13" r="6" fill={fill} />
      <circle cx="13" cy="13" r="3" fill={inner} />
      <circle cx="35" cy="13" r="3" fill={inner} />
    </>
  ),
  bunny: (fill, inner) => (
    <>
      <ellipse cx="16.5" cy="9.5" rx="4.6" ry="9" transform="rotate(-12 16.5 9.5)" fill={fill} />
      <ellipse cx="31.5" cy="9.5" rx="4.6" ry="9" transform="rotate(12 31.5 9.5)" fill={fill} />
      <ellipse cx="16.5" cy="10" rx="2.1" ry="6" transform="rotate(-12 16.5 10)" fill={inner} />
      <ellipse cx="31.5" cy="10" rx="2.1" ry="6" transform="rotate(12 31.5 10)" fill={inner} />
    </>
  ),
  sprout: () => (
    <>
      <path d="M24 13 V7.5" stroke="#6fae7d" strokeWidth="2" strokeLinecap="round" />
      <path d="M24 8 Q18 8.6 16.6 3.4 Q22.6 2.4 24 8 Z M24 8 Q30 8.6 31.4 3.4 Q25.4 2.4 24 8 Z" fill="#86c893" />
    </>
  ),
}

/** The little face shown for a bot without a picture: in its colour, with ears of its own. Decoration only (the
 * name is always beside it). */
export function FaceGlyph({ face, className }: { face: BotLook['face']; className?: string }) {
  const fill = 'color-mix(in oklch, var(--hue) 42%, white)'
  const inner = 'color-mix(in oklch, var(--hue) 22%, white)'
  return (
    <svg viewBox="0 0 48 48" className={className} aria-hidden focusable="false">
      {TOPS[face](fill, inner)}
      <path d="M24 12 C35 12 41 19 41 28 C41 36.5 34 41 24 41 C14 41 7 36.5 7 28 C7 19 13 12 24 12 Z" fill={fill} />
      <rect x="16" y="23" width="3.6" height="7.4" rx="1.8" fill={EYE} />
      <rect x="28.4" y="23" width="3.6" height="7.4" rx="1.8" fill={EYE} />
      <ellipse cx="13.6" cy="32.4" rx="2.9" ry="1.9" fill={CHEEK} opacity="0.85" />
      <ellipse cx="34.4" cy="32.4" rx="2.9" ry="1.9" fill={CHEEK} opacity="0.85" />
      <path d="M20.6 32.2 Q22.3 34.6 24 32.4 Q25.7 34.6 27.4 32.2" fill="none" stroke={EYE} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
