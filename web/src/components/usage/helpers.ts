import type { UsageProvider } from '@/lib/api'

/** Integer ticks from 0 to a clean maximum at or above `max` (about three steps). */
export function niceTicks(max: number): number[] {
  const raw = Math.max(max, 1) / 3
  const pow = 10 ** Math.floor(Math.log10(raw))
  const f = raw / pow
  const step = Math.max(1, (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow)
  const top = Math.max(step, Math.ceil(max / step) * step)
  const ticks: number[] = []
  for (let t = 0; t <= top; t += step) ticks.push(t)
  return ticks
}

/** A model's name on the 用量 page; one no longer configured is marked. */
export const modelLabel = (provider: Pick<UsageProvider, 'provider_id' | 'deleted'>) =>
  provider.deleted ? `${provider.provider_id}（已删除）` : provider.provider_id
