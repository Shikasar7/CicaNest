/** 表情包管理: each companion's own packs of stickers, drawn by nest or uploaded. */

import type { Sticker } from '@/lib/api'

export const stickerImage = (id: number) => `/api/stickers/${id}/image`

export type SlotState = 'empty' | 'queued' | 'drawing' | 'done' | 'failed'

export type Slot = {
  id: number
  pack_id: number
  position: number
  mood: string
  sticker_id: number | null
  state: SlotState
  error: string
  /** 「全部重画」 leaves it. Absent on an older nest. */
  locked?: number
}

export type Pack = {
  id: number
  bot_id: string
  name: string
  style: string
  extra: string
  active: boolean
  created_at: number
  slots: Slot[]
}

/** A sticker pack in an installed plugin. */
export type PluginPack = { plugin: string; plugin_name: string; path: string; name: string; count: number }

export type PacksOverview = {
  bots: { bot_id: string; name: string; pack_id: number | null; done: number; total: number; desk_stickers: boolean }[]
  styles: { id: string; name: string }[]
  moods: string[]
  can_draw: boolean
}

export type BotPacks = {
  packs: Pack[]
  desk_stickers: boolean
  /** 「只用专属表情包里的表情」: in the desk chat she picks only from `own`. */
  own_only: boolean
  /** Her 专属表情包: the pack in use, then the other pictures that are hers alone. */
  own: Sticker[]
  has_avatar: boolean
}

/** A sticker of the desk chat in a message: its picture and how it reads. */
export function stickerOf(segments: { type: string; data: Record<string, unknown> }[]): { id: number; label: string } | null {
  const found = segments.find((s) => s.type === 'sticker')
  const id = found?.data.id
  return typeof id === 'number' ? { id, label: String(found?.data.label ?? '表情') } : null
}
