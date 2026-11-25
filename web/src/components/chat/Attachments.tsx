import { FileText, X } from 'lucide-react'

import { Picture } from '@/components/chat/Picture'
import { mediaUrl } from '@/lib/desk-media'
import type { SentFile, StagedFile } from '@/lib/group-files'
import type { Staged } from '@/lib/staged-pictures'
import { cn } from '@/lib/utils'

/** Pictures and files put by the box of a group, and those of a message. */

function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  return n < 1024 ** 2 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`
}

const cross = 'absolute -top-1.5 -right-1.5 grid size-5 place-items-center rounded-full bg-foreground text-background shadow'

export function Tray({
  pictures,
  files,
  onPicture,
  onFile,
}: {
  pictures: Staged[]
  files: StagedFile[]
  /** Not that one after all. */
  onPicture: (key: number) => void
  onFile: (key: number) => void
}) {
  if (!pictures.length && !files.length) return null
  return (
    <ul aria-label="要发的图片和文件" className="mb-2 flex flex-wrap gap-2 px-1">
      {pictures.map((s) => (
        <li key={`p${s.key}`} className="relative">
          <img src={s.thumb} alt={s.file.name} title={s.file.name} className="size-16 rounded-xl border object-cover" />
          <button type="button" onClick={() => onPicture(s.key)} aria-label={`不发 ${s.file.name}`} className={cross}>
            <X className="size-3" />
          </button>
        </li>
      ))}
      {files.map((s) => (
        <li key={`f${s.key}`} className="relative flex h-16 max-w-52 items-center gap-2 rounded-xl border bg-card px-3 text-xs">
          <FileText aria-hidden className="size-5 shrink-0 text-muted-foreground" />
          <span className="min-w-0">
            <span className="block truncate" title={s.file.name}>{s.file.name}</span>
            <span className="text-muted-foreground">{bytes(s.file.size)}</span>
          </span>
          <button type="button" onClick={() => onFile(s.key)} aria-label={`不发 ${s.file.name}`} className={cross}>
            <X className="size-3" />
          </button>
        </li>
      ))}
    </ul>
  )
}

export function SentPictures({ names, title, mine }: { names: string[]; title: string; mine: boolean }) {
  return (
    <div className={cn('flex flex-wrap gap-1.5', mine && 'justify-end')}>
      {names.map((name) => (
        <Picture key={name} src={mediaUrl(name)} name={name} title={title}>
          <img
            src={mediaUrl(name)}
            alt="图片"
            draggable={false}
            loading="lazy"
            decoding="async"
            className={cn(
              'rounded-2xl border shadow-sm',
              names.length > 1 ? 'size-28 object-cover max-sm:size-24' : 'max-h-60 max-w-60 object-contain',
            )}
          />
        </Picture>
      ))}
    </div>
  )
}

export function SentFiles({ files, mine }: { files: SentFile[]; mine: boolean }) {
  return (
    <ul className={cn('flex max-w-full flex-col gap-1', mine && 'items-end')}>
      {files.map((f, i) => (
        <li
          key={i}
          title="在群里每位伙伴的文件夹「收到的」里"
          className="flex max-w-full items-center gap-2 rounded-2xl border bg-card px-3 py-2 text-xs shadow-sm"
        >
          <FileText aria-hidden className="size-5 shrink-0 text-muted-foreground" />
          <span className="min-w-0">
            <span className="block truncate">{f.name}</span>
            <span className="text-muted-foreground">
              {bytes(f.size)}
              {!f.readable && ' · 群里打不开'}
            </span>
          </span>
        </li>
      ))}
    </ul>
  )
}
