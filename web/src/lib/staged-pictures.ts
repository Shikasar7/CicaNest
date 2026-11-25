import { postRaw } from '@/lib/desk-media'

/** Pictures put by the box, sent together with the words (one message, one answer). */

export type Staged = { key: number; file: File; thumb: string }

export const STAGED_MAX = 9 // in one message (nest.core.desk_media)

const THUMB = 160
let keys = 0
// Uploaded already (a try that failed later): not again.
const uploaded = new WeakMap<File, Promise<string>>()

/** Throws when it is not a picture the browser can show. The small picture is a data: url (the console's policy
 * allows no blob: ones). */
export async function stage(file: File): Promise<Staged> {
  const bitmap = await createImageBitmap(file)
  try {
    const scale = Math.min(1, THUMB / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    return { key: ++keys, file, thumb: canvas.toDataURL('image/png') }
  } finally {
    bitmap.close()
  }
}

/** The picture's name in nest, for the message it goes with. `path`: where it is kept (her chat's, a group's). */
export function upload(path: string, file: File): Promise<string> {
  const known = uploaded.get(file)
  if (known) return known
  const made = postRaw<{ name: string }>(path, file).then((r) => r.name)
  uploaded.set(file, made)
  made.catch(() => uploaded.delete(file))
  return made
}

/** A message that nest no longer has the pictures of: upload them again. */
export const forget = (file: File) => uploaded.delete(file)

/** How such a message reads in the chat log (nest.core.desk.Desk.say). */
export const lineOf = (pictures: number, text: string, files: string[] = []) =>
  ['[图片]'.repeat(pictures) + files.map((name) => `[文件：${name}]`).join(''), text].filter(Boolean).join('\n')
