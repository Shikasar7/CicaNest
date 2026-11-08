import { useCallback, useEffect, useState } from 'react'

import { getJson, type WatchRun, type WatchView } from '@/lib/api'
import { useLive } from '@/lib/live'
import { useLiveData, type LiveData } from '@/lib/live-data'

/** GET /api/watches, refetched whenever a check finishes (and on reconnect). */
export function useWatches(): LiveData<WatchView[]> {
  const { watchesVersion } = useLive()
  return useLiveData<WatchView[]>('/api/watches', [watchesVersion])
}

export const RUNS_LIMIT = 50

export type WatchRuns = {
  /** Newest first; null until the first load. */
  runs: WatchRun[] | null
  error: string
  /** Ids that arrived after the first load: these animate in. */
  fresh: Set<number>
  /** Put a run in front (立即检查's answer); a copy arriving over SSE later is ignored. */
  add: (run: WatchRun) => void
}

function merge(current: WatchRun[], incoming: WatchRun[]): WatchRun[] {
  const byId = new Map(current.map((r) => [r.id, r]))
  for (const r of incoming) byId.set(r.id, r)
  return [...byId.values()].sort((a, b) => b.started_at - a.started_at || b.id - a.id).slice(0, RUNS_LIMIT)
}

/**
 * One watch's checks: loaded once, then live runs prepended. `lastRunId` comes from the list; when it names a
 * run this list has not seen (an SSE event was missed, e.g. during a reconnect), the list is loaded again.
 */
export function useWatchRuns(watchId: number, lastRunId: number | null): WatchRuns {
  const { subscribe } = useLive()
  const [runs, setRuns] = useState<WatchRun[] | null>(null)
  const [error, setError] = useState('')
  const [fresh, setFresh] = useState<Set<number>>(() => new Set())
  // The list's last run this list was found to be missing, so the load below runs again (once per such run).
  const [missedRun, setMissedRun] = useState<number | null>(null)
  if (lastRunId !== null && runs !== null && missedRun !== lastRunId && !runs.some((r) => r.id === lastRunId)) {
    setMissedRun(lastRunId)
  }

  useEffect(() => {
    let cancelled = false
    getJson<WatchRun[]>(`/api/watches/${watchId}/runs?limit=${RUNS_LIMIT}`).then(
      (value) => {
        if (cancelled) return
        setRuns((current) => merge(current ?? [], value))
        setError('')
      },
      (e: Error) => !cancelled && setError(e.message),
    )
    return () => {
      cancelled = true
    }
  }, [watchId, missedRun])

  const add = useCallback((run: WatchRun) => {
    setRuns((current) => (current?.some((r) => r.id === run.id) ? current : merge(current ?? [], [run])))
    setFresh((current) => (current.has(run.id) ? current : new Set(current).add(run.id)))
  }, [])

  useEffect(
    () =>
      subscribe((item) => {
        if (item.kind === 'watch' && item.watchId === watchId) add(item.run)
      }),
    [subscribe, watchId, add],
  )

  return { runs, error, fresh, add }
}
