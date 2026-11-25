/** What the 文件 pane can show of a file in her folder: text as text (never as a page: what she wrote could be
 * HTML) and ordinary pictures. Anything else is for downloading. */

export type FileView = { kind: 'text'; text: string } | { kind: 'image'; src: string } | { kind: 'other'; why: string }

const TEXT_MAX = 1024 ** 2
const IMAGE_MAX = 10 * 1024 ** 2
// No SVG here: it is text that may carry scripts, and is shown as text.
const IMAGES: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
}

const imageType = (name: string) => IMAGES[name.split('.').pop()?.toLowerCase() ?? '']

/** Why it is not fetched at all, or '' when it may be shown. */
export function tooBig(name: string, size: number): string {
  if (imageType(name)) return size > IMAGE_MAX ? '图片太大了，下载下来看吧。' : ''
  return size > TEXT_MAX ? '文件太大了，下载下来看吧。' : ''
}

function decode(bytes: Uint8Array): string | null {
  if (bytes.includes(0)) return null // not a text file
  for (const encoding of ['utf-8', 'gb18030']) {
    try {
      return new TextDecoder(encoding, { fatal: true }).decode(bytes)
    } catch {
      // the next one
    }
  }
  return null
}

function dataUrl(bytes: Uint8Array<ArrayBuffer>, type: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('图片没读出来'))
    reader.readAsDataURL(new Blob([bytes], { type })) // the console's policy allows pictures as data: only
  })
}

export async function viewOf(name: string, bytes: Uint8Array<ArrayBuffer>): Promise<FileView> {
  const why = tooBig(name, bytes.length) // it may have grown since it was listed
  if (why) return { kind: 'other', why }
  const type = imageType(name)
  if (type) return { kind: 'image', src: await dataUrl(bytes, type) }
  const text = decode(bytes)
  return text === null ? { kind: 'other', why: '这种文件在这里打不开，下载下来看吧。' } : { kind: 'text', text }
}
