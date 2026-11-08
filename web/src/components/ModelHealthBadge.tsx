import { Badge } from '@/components/ui/badge'
import type { ProviderHealth } from '@/lib/api'

/**
 * How a loaded model has been doing since it was (re)loaded. Every save on the 模型 page loads a fresh copy,
 * so "never called" is normal right after an edit.
 */
export function ModelHealthBadge({ health }: { health: ProviderHealth | null }) {
  if (!health) return null
  if (!health.available) {
    return <Badge className="bg-amber-500 text-white">连续失败，{health.retry_in} 秒后再试</Badge>
  }
  if (health.consecutive_failures > 0) return <Badge className="bg-amber-500 text-white">最近失败过</Badge>
  if (health.last_ok_at) return <Badge variant="outline">可用</Badge>
  return <Badge variant="outline">还没调用过</Badge>
}
