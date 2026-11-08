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

/** Fetch `path` now and again whenever one of `versions` bumps (debounced). Late responses are dropped. */
export function useLiveData<T>(path: string, versions: number[]): LiveData<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [missing, setMissing] = useState(false)
  const [nonce, setNonce] = useState(0)
  const latest = useRef(0)
  const key = versions.join(',')
  useEffect(() => {
    const timer = setTimeout(() => {
      const request = ++latest.current
      getJson<T>(path).then(
        (value) => {
          if (request !== latest.current) return
          setData(value)
          setError('')
          setMissing(false)
        },
        (e: Error) => {
          if (request !== latest.current) return
          setError(e.message)
          setMissing(e instanceof ApiError && e.status === 404)
        },
      )
    }, 300)
    return () => clearTimeout(timer)
  }, [path, key, nonce])
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, missing, reload }
}
