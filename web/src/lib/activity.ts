import { useEffect, useMemo, useState } from 'react'

import { getJson, type Decision, type Group, type Timeline } from '@/lib/api'
import { useLive } from '@/lib/live'

export type Activity = {
  decisions: Decision[]
  /** channel id → the group's name, for "在 某某群". */
  groupOf: Map<number, string>
  /** Ids that arrived live after the first load: these animate in. */
  fresh: Set<number>
  loaded: boolean
}

/** The latest decisions of every bot in every group: a first load from the group timelines, then live events. */
export function useRecentDecisions(limit = 30): Activity {
  const { subscribe, channelsVersion } = useLive()
  const [groups, setGroups] = useState<Group[]>([])
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [loaded, setLoaded] = useState(false)
  const [fresh, setFresh] = useState<Set<number>>(() => new Set())

  useEffect(() => {
    let cancelled = false
    getJson<Group[]>('/api/groups').then(
      async (list) => {
        if (cancelled) return
        setGroups(list)
        const timelines = await Promise.allSettled(
          list
            .filter((g) => g.kind === 'group')
            .map((g) => getJson<Timeline>(`/api/groups/${encodeURIComponent(g.key)}/timeline?limit=40`)),
        )
        if (cancelled) return
        const all = timelines.flatMap((t) => (t.status === 'fulfilled' ? t.value.decisions : []))
        setDecisions((current) => merge(current, all, limit))
        setLoaded(true)
      },
      () => !cancelled && setLoaded(true),
    )
    return () => {
      cancelled = true
    }
  }, [channelsVersion, limit])

  useEffect(
    () =>
      subscribe((item) => {
        if (item.kind !== 'decision') return
        setFresh((current) => new Set(current).add(item.decision.id))
        setDecisions((current) => merge(current, [item.decision], limit))
      }),
    [subscribe, limit],
  )

  const groupOf = useMemo(() => {
    const map = new Map<number, string>()
    for (const g of groups)
      for (const c of g.channels)
        map.set(c.channel_id, g.kind === 'group' ? g.name || `群 ${g.external_id}` : `和 ${g.name || g.external_id} 的私聊`)
    return map
  }, [groups])

  return { decisions, groupOf, fresh, loaded }
}

function merge(current: Decision[], incoming: Decision[], limit: number): Decision[] {
  const byId = new Map(current.map((d) => [d.id, d]))
  for (const d of incoming) byId.set(d.id, d)
  return [...byId.values()].sort((a, b) => b.created_at - a.created_at || b.id - a.id).slice(0, limit)
}
