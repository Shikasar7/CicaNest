import { useCallback, useEffect, useState } from 'react'

import { getJson, type GameView } from '@/lib/api'
import { useSubscribe } from '@/lib/live'

const newer = (a: GameView | null, b: GameView) =>
  !a || b.id > a.id || (b.id === a.id && (b.updated_at > a.updated_at || (b.updated_at === a.updated_at && !b.thinking)))

/** Her newest game, kept current by the "game" events. */
export function useGame(botId: string) {
  const subscribe = useSubscribe()
  const [game, setGame] = useState<GameView | null>(null)
  const [loaded, setLoaded] = useState(false)
  const take = useCallback((next: GameView) => setGame((current) => (newer(current, next) ? next : current)), [])
  useEffect(() => {
    let cancelled = false
    getJson<{ game: GameView | null }>(`/api/games/current?bot_id=${encodeURIComponent(botId)}`).then(
      (found) => {
        if (cancelled) return
        if (found.game) take(found.game)
        setLoaded(true)
      },
      () => !cancelled && setLoaded(true),
    )
    return () => {
      cancelled = true
    }
  }, [botId, take])
  useEffect(
    () =>
      subscribe((item) => {
        if (item.kind === 'game' && item.game.bot_id === botId) take(item.game)
      }),
    [subscribe, botId, take],
  )
  return { game, loaded, take }
}
