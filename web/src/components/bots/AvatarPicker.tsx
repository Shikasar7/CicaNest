import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { Check, Download, LoaderCircle, Plus } from 'lucide-react'

import { AvatarCropper } from '@/components/bots/AvatarCropper'
import { BotGlyph } from '@/components/BotGlyph'
import { Button } from '@/components/ui/button'
import { avatarPath, avatarUrl, sendBlob, sendJson, type AvatarView, type Bot } from '@/lib/api'
import { useLive } from '@/lib/live'
import { cn } from '@/lib/utils'

const TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']
const MAX_FILE = 20 * 1024 * 1024 // before cropping; what is uploaded is a 512 px square
const NONE: AvatarView = { chosen: null, qq: null, custom: null }

type Busy = 'glyph' | 'qq' | 'custom' | 'refetch' | 'delete' | 'upload' | null

function readPicture(file: File): Promise<string> {
  if (!TYPES.includes(file.type)) return Promise.reject(new Error('只能用 PNG、JPEG、WebP 或 GIF 图片'))
  if (file.size > MAX_FILE) return Promise.reject(new Error('图片太大了（超过 20 MB），换一张小一点的'))
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(new Error('读不了这个文件'))
    reader.readAsDataURL(file)
  })
}

function Choice({
  label,
  selected,
  busy,
  disabled,
  onClick,
  children,
}: {
  label: string
  selected: boolean
  busy: boolean
  disabled: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'relative flex w-full min-w-0 flex-col items-center gap-2 rounded-xl px-2 pt-3 pb-2 text-xs transition-[background-color,color,box-shadow] duration-200 outline-none focus-visible:ring-2 focus-visible:ring-[var(--hue)] disabled:cursor-default',
        selected
          ? 'bg-[color-mix(in_srgb,var(--hue)_9%,transparent)] font-medium text-foreground ring-1 ring-[color-mix(in_srgb,var(--hue)_55%,transparent)]'
          : 'text-muted-foreground enabled:hover:bg-muted enabled:hover:text-foreground',
      )}
    >
      <span className="relative grid size-14 place-items-center">
        {children}
        {busy && (
          <span className="absolute inset-0 grid place-items-center rounded-full bg-card/70">
            <LoaderCircle className="size-5 animate-spin text-[var(--hue)]" />
          </span>
        )}
      </span>
      {label}
      <Check
        aria-hidden
        className={cn(
          'absolute top-1.5 right-1.5 size-3.5 text-[var(--hue)] transition-[opacity,scale] duration-200',
          selected ? 'scale-100 opacity-100' : 'scale-50 opacity-0',
        )}
      />
    </button>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <span className="grid size-14 place-items-center rounded-full border border-dashed border-muted-foreground/40 text-muted-foreground">
      {children}
    </span>
  )
}

/** Which picture the bot shows in the console: its brush character, its QQ avatar (fetched from QQ when
 * first chosen), or one the user picks, drops or pastes and crops here. */
export function AvatarPicker({ bot, onDone }: { bot: Bot; onDone: () => void }) {
  const { avatars, setAvatar } = useLive()
  const view = avatars[bot.id] ?? NONE
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState('')
  const [cropping, setCropping] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const path = avatarPath(bot.id)
  const isQQ = bot.id.startsWith('qq:')

  const run = async (kind: Busy, action: () => Promise<AvatarView>): Promise<boolean> => {
    if (busy) return false
    setBusy(kind)
    setError('')
    try {
      setAvatar(bot.id, await action())
      return true
    } catch (e) {
      setError((e as Error).message)
      return false
    } finally {
      setBusy(null)
    }
  }
  const choose = (chosen: 'glyph' | 'qq' | 'custom') =>
    run(chosen, () => sendJson<AvatarView>('PUT', path, { chosen }))
  const fetchQQ = (kind: Busy) => run(kind, () => sendJson<AvatarView>('POST', `${path}/qq`))

  const pick = async (file: File | undefined) => {
    if (!file) return
    setError('')
    try {
      setCropping(await readPicture(file))
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const upload = async (picture: Blob) => {
    if (await run('upload', () => sendBlob<AvatarView>(`${path}/custom`, picture))) setCropping(null)
  }

  // A picture copied anywhere (a screenshot, an image from a web page) can be pasted straight in.
  useEffect(() => {
    if (cropping) return
    const onPaste = (event: ClipboardEvent) => {
      const file = [...(event.clipboardData?.files ?? [])].find((f) => f.type.startsWith('image/'))
      if (!file) return
      event.preventDefault()
      void pick(file)
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [cropping])

  const hasFiles = (event: DragEvent) => event.dataTransfer.types.includes('Files')
  const dropZone = {
    onDragEnter: (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      setDragging(true)
    },
    onDragOver: (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      event.dataTransfer.dropEffect = 'copy'
    },
    onDragLeave: (event: DragEvent) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
    },
    onDrop: (event: DragEvent) => {
      event.preventDefault()
      setDragging(false)
      void pick(event.dataTransfer.files[0])
    },
  }

  const qqUrl = view.qq != null ? avatarUrl(bot.id, 'qq', view.qq) : null
  const customUrl = view.custom != null ? avatarUrl(bot.id, 'custom', view.custom) : null

  return (
    <section aria-label={`${bot.name} 的头像`} className="feed-in">
      <div
        {...dropZone}
        className={cn(
          'relative rounded-xl border bg-muted/30 p-4 transition-[border-color,background-color] duration-200',
          dragging && 'border-dashed border-[var(--hue)] bg-[color-mix(in_srgb,var(--hue)_6%,transparent)]',
        )}
      >
        <div className={cn('transition-opacity duration-200', dragging && 'opacity-20')}>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-medium">{cropping ? '调整一下位置和大小' : '头像'}</h3>
            {!cropping && (
              <Button size="sm" variant="ghost" onClick={onDone}>
                完成
              </Button>
            )}
          </div>
          <input
            ref={input}
            type="file"
            accept={TYPES.join(',')}
            className="hidden"
            onChange={(e) => {
              void pick(e.target.files?.[0])
              e.target.value = '' // the same file again still counts as a change
            }}
          />
          {cropping ? (
            <AvatarCropper
              src={cropping}
              busy={busy === 'upload'}
              onCancel={() => setCropping(null)}
              onPickAnother={() => input.current?.click()}
              onConfirm={(picture) => void upload(picture)}
              onError={(message) => {
                setError(message)
                setCropping(null)
              }}
            />
          ) : (
            <>
              <div role="radiogroup" aria-label="显示哪个头像" className="grid grid-cols-[repeat(3,minmax(0,5.5rem))] gap-2">
                <Choice
                  label="字"
                  selected={view.chosen === null}
                  busy={busy === 'glyph'}
                  disabled={busy !== null}
                  onClick={() => view.chosen !== null && void choose('glyph')}
                >
                  <BotGlyph name={bot.name} id={bot.id} size="lg" picture={null} />
                </Choice>
                {isQQ && (
                  <Choice
                    label="QQ 头像"
                    selected={view.chosen === 'qq'}
                    busy={busy === 'qq'}
                    disabled={busy !== null}
                    onClick={() => view.chosen !== 'qq' && void (qqUrl ? choose('qq') : fetchQQ('qq'))}
                  >
                    {qqUrl ? (
                      <BotGlyph name={bot.name} id={bot.id} size="lg" picture={qqUrl} />
                    ) : (
                      <Empty>
                        <Download className="size-4" aria-hidden />
                      </Empty>
                    )}
                  </Choice>
                )}
                <Choice
                  label="自己的图片"
                  selected={view.chosen === 'custom'}
                  busy={busy === 'custom' || busy === 'delete'}
                  disabled={busy !== null}
                  onClick={() => (customUrl ? view.chosen !== 'custom' && void choose('custom') : input.current?.click())}
                >
                  {customUrl ? (
                    <BotGlyph name={bot.name} id={bot.id} size="lg" picture={customUrl} />
                  ) : (
                    <Empty>
                      <Plus className="size-4" aria-hidden />
                    </Empty>
                  )}
                </Choice>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-x-1 gap-y-1">
                {isQQ && qqUrl && (
                  <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void fetchQQ('refetch')}>
                    {busy === 'refetch' && <LoaderCircle className="size-3.5 animate-spin" />}
                    重新获取 QQ 头像
                  </Button>
                )}
                {customUrl && (
                  <>
                    <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => input.current?.click()}>
                      换一张图片
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy !== null}
                      onClick={() => void run('delete', () => sendJson<AvatarView>('DELETE', `${path}/custom`))}
                    >
                      删除图片
                    </Button>
                  </>
                )}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                也可以把图片拖到这里，或者直接粘贴。只改控制台里的样子，不会改 QQ 上的头像。
              </p>
            </>
          )}
          {error && (
            <p role="alert" className="mt-3 text-xs text-destructive">
              {error}
            </p>
          )}
        </div>
        {dragging && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center rounded-xl text-sm font-medium text-[var(--hue)]">
            <span className="rounded-full bg-card px-3 py-1 shadow-[var(--shadow-lift)]">松开就能用这张图</span>
          </div>
        )}
      </div>
    </section>
  )
}
