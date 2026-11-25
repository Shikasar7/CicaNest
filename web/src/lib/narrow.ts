import { useSyncExternalStore } from 'react'

const PHONE = '(max-width: 639px)'

/** A phone-sized window (narrower than Tailwind's `sm`): the chat shows the list and a conversation as two
 * screens instead of side by side. */
export function useNarrow(query: string = PHONE): boolean {
  return useSyncExternalStore(
    (changed) => {
      const list = window.matchMedia(query)
      list.addEventListener('change', changed)
      return () => list.removeEventListener('change', changed)
    },
    () => window.matchMedia(query).matches,
    () => false,
  )
}
