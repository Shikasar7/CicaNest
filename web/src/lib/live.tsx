import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import {
  ApiError,
  avatarUrl,
  getJson,
  type Account,
  type AccountStatus,
  type AvatarView,
  type Decision,
  type LogLine,
  type Message,
  type Persona,
  type PlatformView,
  type ProviderView,
  type Tone,
  type WatchEvent,
  type WatchRun,
} from '@/lib/api'
import { useLiveData, type LiveData } from '@/lib/live-data'

export type { LiveData } from '@/lib/live-data'

/** 'open': receiving live updates; 'login': session missing/expired; 'down': backend unreachable. */
export type LiveState = 'connecting' | 'open' | 'login' | 'down'

export type LiveItem =
  | { kind: 'message' | 'recall'; message: Message }
  | { kind: 'decision'; decision: Decision }
  | { kind: 'log'; line: LogLine }
  | { kind: 'watch'; watchId: number; run: WatchRun }

type Listener = (item: LiveItem) => void

type Live = {
  state: LiveState
  accounts: Account[]
  /** Bumps when the channel list may have changed; use as an effect dependency to refetch. */
  channelsVersion: number
  /** Bumps when bot settings or today's counts may have changed. */
  botsVersion: number
  /** Bumps when a model's settings or health may have changed. */
  providersVersion: number
  /** Bumps when SnowLuma, QQ or nest's own settings may have changed. */
  systemVersion: number
  /** Bumps when a 监控 finished a check (its last run and next check time changed). */
  watchesVersion: number
  /** Bumps when the 表情包 collection, its settings or a group's switches changed. */
  stickersVersion: number
  /** Bumps when a group's 黑话, the learning model or a group's switches changed. */
  slangVersion: number
  /** Bumps when 问题记录 changed: an issue written down, its status, the digest settings or a digest sent. */
  issuesVersion: number
  /** Which picture each bot shows (bot id -> view); empty until loaded, or on a nest from before pictures. */
  avatars: Record<string, AvatarView>
  /** Show what a picture change answered at once, until the refetch its own event triggers arrives. */
  setAvatar: (botId: string, view: AvatarView) => void
  /** Refetch bots, models and personas now (e.g. after 测试一下, which publishes no event). */
  refreshConfig: () => void
  subscribe: (listener: Listener) => () => void
}

const LiveContext = createContext<Live | null>(null)
const NO_AVATARS: Record<string, AvatarView> = {}
// How long a tab may sit in the background before it lets go of its live connection.
const IDLE_AFTER_MS = 30_000

/** False once the tab has been in the background for IDLE_AFTER_MS; true again as soon as it is shown. Over
 * plain http a browser keeps at most 6 connections to one address, and every console tab holds one for its
 * live updates: with several tabs open, the next page's requests would wait for a free one. */
function usePageActive() {
  const [active, setActive] = useState(true)
  useEffect(() => {
    let timer: number | undefined
    const changed = () => {
      window.clearTimeout(timer)
      if (document.visibilityState === 'visible') setActive(true)
      else timer = window.setTimeout(() => setActive(false), IDLE_AFTER_MS)
    }
    changed()
    document.addEventListener('visibilitychange', changed)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', changed)
    }
  }, [])
  return active
}

/** One EventSource for the whole console: account status, messages, recalls, bot decisions. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<LiveState>('connecting')
  const [accounts, setAccounts] = useState<Account[]>([])
  const [channelsVersion, setChannelsVersion] = useState(0)
  const [botsVersion, setBotsVersion] = useState(0)
  const [providersVersion, setProvidersVersion] = useState(0)
  const [systemVersion, setSystemVersion] = useState(0)
  const [watchesVersion, setWatchesVersion] = useState(0)
  const [stickersVersion, setStickersVersion] = useState(0)
  const [slangVersion, setSlangVersion] = useState(0)
  const [issuesVersion, setIssuesVersion] = useState(0)
  const [avatarsVersion, setAvatarsVersion] = useState(0)
  const fetchedAvatars = useLiveData<Record<string, AvatarView>>('/api/avatars', [avatarsVersion]).data
  // Answers of picture changes, laid over the fetch they arrived after; any newer fetch replaces them.
  const [avatarPatch, setAvatarPatch] = useState<{ base: unknown; views: Record<string, AvatarView> }>({
    base: null,
    views: {},
  })
  const avatars = useMemo(() => {
    const base = fetchedAvatars ?? NO_AVATARS
    return avatarPatch.base === fetchedAvatars ? { ...base, ...avatarPatch.views } : base
  }, [fetchedAvatars, avatarPatch])
  const setAvatar = useCallback(
    (botId: string, view: AvatarView) =>
      setAvatarPatch((p) => ({
        base: fetchedAvatars,
        views: { ...(p.base === fetchedAvatars ? p.views : {}), [botId]: view },
      })),
    [fetchedAvatars],
  )
  const listeners = useRef(new Set<Listener>())
  const active = usePageActive()

  useEffect(() => {
    // A background tab closes its connection; shown again, it reconnects and "hello" refetches every page.
    if (!active) return
    const source = new EventSource('/api/events')
    const json = (event: Event) => JSON.parse((event as MessageEvent<string>).data)
    const emit = (item: LiveItem) => {
      for (const listener of listeners.current) listener(item)
    }

    source.addEventListener('hello', (event) => {
      setState('open')
      setAccounts(json(event).accounts as Account[])
      setChannelsVersion((v) => v + 1)
      setBotsVersion((v) => v + 1)
      setProvidersVersion((v) => v + 1)
      setSystemVersion((v) => v + 1)
      setWatchesVersion((v) => v + 1)
      setAvatarsVersion((v) => v + 1)
      setStickersVersion((v) => v + 1)
      setSlangVersion((v) => v + 1)
    })
    source.addEventListener('status', (event) => {
      const update = json(event) as AccountStatus & { account_id: string }
      setAccounts((current) =>
        current.map((account) =>
          account.id === update.account_id
            ? { ...account, status: { state: update.state, detail: update.detail, since: update.since } }
            : account,
        ),
      )
    })
    source.addEventListener('channels', () => setChannelsVersion((v) => v + 1))
    source.addEventListener('bots', () => setBotsVersion((v) => v + 1))
    source.addEventListener('providers', () => setProvidersVersion((v) => v + 1))
    source.addEventListener('foreign', () => setBotsVersion((v) => v + 1))
    for (const kind of ['message', 'recall'] as const) {
      source.addEventListener(kind, (event) => {
        if (kind === 'message') setChannelsVersion((v) => v + 1)
        emit({ kind, message: json(event) as Message })
      })
    }
    source.addEventListener('system', () => setSystemVersion((v) => v + 1))
    source.addEventListener('avatars', () => setAvatarsVersion((v) => v + 1))
    source.addEventListener('stickers', () => setStickersVersion((v) => v + 1))
    source.addEventListener('slang', () => setSlangVersion((v) => v + 1))
    source.addEventListener('issues', () => setIssuesVersion((v) => v + 1))
    source.addEventListener('groups', () => {
      setStickersVersion((v) => v + 1)
      setSlangVersion((v) => v + 1)
    })
    source.addEventListener('log', (event) => emit({ kind: 'log', line: json(event) as LogLine }))
    source.addEventListener('decision', (event) => {
      setBotsVersion((v) => v + 1)
      emit({ kind: 'decision', decision: json(event) as Decision })
    })
    source.addEventListener('watch', (event) => {
      const update = json(event) as WatchEvent
      setWatchesVersion((v) => v + 1)
      emit({ kind: 'watch', watchId: update.watch_id, run: update.run })
    })
    source.onerror = () => {
      // EventSource retries by itself; find out whether this is a login problem or the backend being down.
      getJson('/api/status').then(
        () => setState('connecting'),
        (error) => setState(error instanceof ApiError && error.status === 401 ? 'login' : 'down'),
      )
    }
    return () => source.close()
  }, [active])

  const subscribe = useCallback((listener: Listener) => {
    listeners.current.add(listener)
    return () => listeners.current.delete(listener)
  }, [])

  const refreshConfig = useCallback(() => {
    setBotsVersion((v) => v + 1)
    setProvidersVersion((v) => v + 1)
  }, [])

  return (
    <LiveContext.Provider
      value={{
        state,
        accounts,
        channelsVersion,
        botsVersion,
        providersVersion,
        systemVersion,
        watchesVersion,
        stickersVersion,
        slangVersion,
        issuesVersion,
        avatars,
        setAvatar,
        refreshConfig,
        subscribe,
      }}
    >
      {children}
    </LiveContext.Provider>
  )
}

/** The address of the picture `botId` shows, or null for its brush character. Works outside the provider
 * too (no pictures there). */
export function useAvatarUrl(botId: string | undefined): string | null {
  const view = useContext(LiveContext)?.avatars[botId ?? '']
  const source = view?.chosen
  const version = source ? view[source] : null
  return botId && source && version != null ? avatarUrl(botId, source, version) : null
}

export function useLive(): Live {
  const live = useContext(LiveContext)
  if (!live) throw new Error('useLive must be used inside <LiveProvider>')
  return live
}

/** Fetch bots now and whenever the live stream says they (or their models) changed. */
export function useBots<T>(path: string): LiveData<T> {
  const { botsVersion, providersVersion } = useLive()
  return useLiveData<T>(path, [botsVersion, providersVersion])
}

/** GET /api/providers, refetched like useBots. */
export function useProviders(): LiveData<ProviderView[]> {
  const { botsVersion, providersVersion } = useLive()
  return useLiveData<ProviderView[]>('/api/providers', [botsVersion, providersVersion])
}

/** GET /api/platforms: fixed while nest runs. */
export function usePlatforms(): LiveData<PlatformView[]> {
  return useLiveData<PlatformView[]>('/api/platforms', [])
}

/** GET /api/tones, refetched like useBots (tone edits publish a bots event). */
export function useTones(): LiveData<Tone[]> {
  const { botsVersion, providersVersion } = useLive()
  return useLiveData<Tone[]>('/api/tones', [botsVersion, providersVersion])
}

/** GET /api/personas, refetched like useBots (persona edits publish a bots event). */
export function usePersonas(): LiveData<Persona[]> {
  const { botsVersion, providersVersion } = useLive()
  return useLiveData<Persona[]>('/api/personas', [botsVersion, providersVersion])
}
