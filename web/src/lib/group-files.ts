import { postRaw } from '@/lib/desk-media'

/** Files sent in a 桌面群 (nest.core.desk_files): what a text file says goes with the message, the file itself
 * into every companion's folder. */

export type StagedFile = { key: number; file: File }
export type SentFile = { name: string; size: number; readable: boolean }

export const FILES_MAX = 5 // in one message

let keys = 0
export const stageFile = (file: File): StagedFile => ({ key: ++keys, file })

type Segments = { type: string; data: Record<string, unknown> }[]

export const filesOf = (segments: Segments | undefined): SentFile[] =>
  (segments ?? [])
    .filter((s) => s.type === 'desk-file')
    .map((s) => ({ name: String(s.data.name ?? ''), size: Number(s.data.size ?? 0), readable: Boolean(s.data.text) }))

export type Kept = { name: string; title: string }

/** Kept by nest until the message is sent. */
export const keepFile = (groupId: number, file: File) =>
  postRaw<Kept>(`/api/desk/groups/${groupId}/files?name=${encodeURIComponent(file.name)}`, file)
