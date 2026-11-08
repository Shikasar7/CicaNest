import type { StickerStatus } from '@/lib/api'

export const STATUS: Record<StickerStatus, string> = {
  ready: '可用',
  pending: '待描述',
  unsuitable: '不适合',
  failed: '描述失败',
}
