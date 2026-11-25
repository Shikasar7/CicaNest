import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, getJson } from '@/lib/api'

export type LiveData<T> = {
  /** The last good response; kept when a later refetch fails (null only until the first one succeeds). */
  data: T | null
  /** The latest refetch's error, '' once one succeeds. Show it above `data`, never instead of it. */
  error: string
  /** The latest refetch answered 404: the running nest is older than this console and has no such endpoint. */
  missing: boolean
  reload: () => void
}

// What each path answered last, so a page opened again shows it at once while it is fetched afresh
// (rather than an empty page for as long as the request takes).
const answered = new Map<string, unknown>()
const ANSWERED_MAX = 200
const DEBOUNCE_MS = 300 // refetches a burst of live events asks for become one

/** Fetch `path` now and again whenever one of `versions` bumps (debounced). Late responses are dropped.
 * The first fetch of a path (and `reload`) goes out at once; until it answers, what the path answered
 * last time (in this window) is shown. */
export function useLiveData<T>(path: string, versions: number[]): LiveData<T> {
  const [shown, setShown] = useState<{ path: string; data: T | null }>(() => ({
    path,
    data: (answered.get(path) as T | undefined) ?? null,
  }))
  const [error, setError] = useState('')
  const [missing, setMissing] = useState(false)
  const [nonce, setNonce] = useState(0)
  const latest = useRef(0)
  const last = useRef<{ path: string; nonce: number } | null>(null)
  const key = versions.join(',')
  useEffect(() => {
    const fetch = () => {
      const request = ++latest.current
      getJson<T>(path).then(
        (value) => {
          if (request !== latest.current) return
          if (answered.size >= ANSWERED_MAX) answered.delete(answered.keys().next().value as string)
          answered.set(path, value)
          setShown({ path, data: value })
          setError('')
          setMissing(false)
        },
        (e: Error) => {
          if (request !== latest.current) return
          setError(e.message)
          setMissing(e instanceof ApiError && e.status === 404)
        },
      )
    }
    const fresh = last.current === null || last.current.path !== path || last.current.nonce !== nonce
    last.current = { path, nonce }
    if (fresh) {
      fetch()
      return
    }
    const timer = setTimeout(fetch, DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [path, key, nonce])
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  const data = shown.path === path ? shown.data : ((answered.get(path) as T | undefined) ?? null)
  return { data, error, missing, reload }
}
