import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from 'react'
import { LoaderCircle, ZoomIn, ZoomOut } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const STAGE = 240 // the crop area, px
const OUTPUT = 512 // the square that is uploaded
const MAX_ZOOM = 4
const PREVIEWS = [56, 36, 24] // the glyph sizes the console shows a bot at (lg, md, sm)

type Size = { w: number; h: number }
/** zoom: 1 = the picture just covers the circle; x, y: its centre's offset from the circle's, in stage px. */
type Crop = { zoom: number; x: number; y: number }

const cover = (size: Size) => STAGE / Math.min(size.w, size.h)
const clampTo = (value: number, limit: number) => Math.min(limit, Math.max(-limit, value))

/** Keep the picture covering the whole circle. */
function clamp(crop: Crop, size: Size): Crop {
  const zoom = Math.min(MAX_ZOOM, Math.max(1, crop.zoom))
  const scale = cover(size) * zoom
  return {
    zoom,
    x: clampTo(crop.x, (size.w * scale - STAGE) / 2),
    y: clampTo(crop.y, (size.h * scale - STAGE) / 2),
  }
}

/** Zoom to `zoom`, keeping the stage point (px, py) (relative to its centre) where it is. */
function zoomAt(crop: Crop, size: Size, zoom: number, px = 0, py = 0): Crop {
  const next = Math.min(MAX_ZOOM, Math.max(1, zoom))
  const k = next / crop.zoom
  return clamp({ zoom: next, x: px - (px - crop.x) * k, y: py - (py - crop.y) * k }, size)
}

/** Where the picture sits inside a `box`-px view of the stage. */
function placed(size: Size, crop: Crop, box: number): CSSProperties {
  const k = box / STAGE
  const scale = cover(size) * crop.zoom * k
  const width = size.w * scale
  const height = size.h * scale
  return { width, height, left: box / 2 + crop.x * k - width / 2, top: box / 2 + crop.y * k - height / 2 }
}

async function render(image: HTMLImageElement, size: Size, crop: Crop): Promise<Blob> {
  const scale = cover(size) * crop.zoom
  const left = STAGE / 2 + crop.x - (size.w * scale) / 2
  const top = STAGE / 2 + crop.y - (size.h * scale) / 2
  const canvas = document.createElement('canvas')
  canvas.width = OUTPUT
  canvas.height = OUTPUT
  const context = canvas.getContext('2d')
  if (!context) throw new Error('浏览器不能处理这张图片')
  context.imageSmoothingQuality = 'high'
  context.drawImage(image, -left / scale, -top / scale, STAGE / scale, STAGE / scale, 0, 0, OUTPUT, OUTPUT)
  const encode = (type: string, quality?: number) =>
    new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, type, quality))
  const webp = await encode('image/webp', 0.9)
  const blob = webp?.type === 'image/webp' ? webp : await encode('image/png')
  if (!blob) throw new Error('浏览器不能处理这张图片')
  return blob
}

/** Square crop of a picked picture, shown through a circle like everywhere else: drag to move, wheel,
 * slider or +/- to zoom, arrow keys to nudge. Previews show it at the sizes the console uses. */
export function AvatarCropper({
  src,
  busy,
  onCancel,
  onPickAnother,
  onConfirm,
  onError,
}: {
  /** A data: URL (the console's CSP allows data: pictures, not blob:). */
  src: string
  busy: boolean
  onCancel: () => void
  onPickAnother: () => void
  onConfirm: (picture: Blob) => void
  onError: (message: string) => void
}) {
  const [image, setImage] = useState<{ element: HTMLImageElement; size: Size } | null>(null)
  const [crop, setCrop] = useState<Crop>({ zoom: 1, x: 0, y: 0 })
  const [rendering, setRendering] = useState(false)
  const stage = useRef<HTMLDivElement>(null)
  const drag = useRef<{ pointer: number; x: number; y: number; from: Crop } | null>(null)
  const errorRef = useRef(onError)
  useEffect(() => {
    errorRef.current = onError
  })

  useEffect(() => {
    let current = true
    const element = new Image()
    element.onload = () => {
      if (!current) return
      setImage({ element, size: { w: element.naturalWidth, h: element.naturalHeight } })
      setCrop({ zoom: 1, x: 0, y: 0 })
    }
    element.onerror = () => current && errorRef.current('这张图片打不开，换一张试试')
    element.src = src
    return () => {
      current = false
    }
  }, [src])

  // React's onWheel is passive, so the page would scroll too: listen directly to keep the wheel here.
  useEffect(() => {
    const node = stage.current
    if (!node || !image) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const box = node.getBoundingClientRect()
      const px = event.clientX - box.left - STAGE / 2
      const py = event.clientY - box.top - STAGE / 2
      setCrop((c) => zoomAt(c, image.size, c.zoom * Math.exp(-event.deltaY * 0.0015), px, py))
    }
    node.addEventListener('wheel', onWheel, { passive: false })
    return () => node.removeEventListener('wheel', onWheel)
  }, [image])

  if (!image) {
    return (
      <div className="grid h-[240px] place-items-center text-muted-foreground">
        <LoaderCircle className="size-5 animate-spin" />
      </div>
    )
  }
  const { size } = image

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, from: crop }
  }
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.pointer !== event.pointerId) return
    setCrop(clamp({ ...d.from, x: d.from.x + event.clientX - d.x, y: d.from.y + event.clientY - d.y }, size))
  }
  const onPointerUp = () => {
    drag.current = null
  }
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 32 : 8
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [step, 0],
      ArrowRight: [-step, 0],
      ArrowUp: [0, step],
      ArrowDown: [0, -step],
    }
    const move = moves[event.key]
    if (move) setCrop((c) => clamp({ ...c, x: c.x + move[0], y: c.y + move[1] }, size))
    else if (event.key === '+' || event.key === '=') setCrop((c) => zoomAt(c, size, c.zoom * 1.15))
    else if (event.key === '-') setCrop((c) => zoomAt(c, size, c.zoom / 1.15))
    else if (event.key === '0') setCrop({ zoom: 1, x: 0, y: 0 })
    else return
    event.preventDefault()
  }
  const confirm = async () => {
    setRendering(true)
    try {
      onConfirm(await render(image.element, size, crop))
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setRendering(false)
    }
  }
  const working = busy || rendering

  return (
    <div className="flex flex-wrap items-start gap-6">
      <div className="space-y-3">
        <div
          ref={stage}
          tabIndex={0}
          role="group"
          aria-roledescription="裁剪区域"
          aria-label="拖动调整位置，滚轮或 +、- 缩放，方向键微调，0 复原"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={onKeyDown}
          style={{ width: STAGE, height: STAGE }}
          className="relative cursor-grab touch-none overflow-hidden rounded-xl bg-muted outline-none select-none active:cursor-grabbing focus-visible:ring-2 focus-visible:ring-[var(--hue)] focus-visible:ring-offset-2 focus-visible:ring-offset-card"
        >
          <img src={src} alt="" draggable={false} style={placed(size, crop, STAGE)} className="pointer-events-none absolute max-w-none" />
          <div className="pointer-events-none absolute inset-0 rounded-full shadow-[0_0_0_999px_rgb(15_20_26/0.5)] ring-1 ring-white/70" />
        </div>
        <label className="flex items-center gap-2 text-muted-foreground" style={{ width: STAGE }}>
          <ZoomOut className="size-4 shrink-0" aria-hidden />
          <input
            type="range"
            min={1}
            max={MAX_ZOOM}
            step={0.01}
            value={crop.zoom}
            aria-label="缩放"
            onChange={(e) => setCrop((c) => zoomAt(c, size, Number(e.target.value)))}
            className="h-1 w-full cursor-pointer accent-[var(--hue)]"
          />
          <ZoomIn className="size-4 shrink-0" aria-hidden />
        </label>
      </div>
      <div className="flex min-w-40 flex-1 flex-col gap-5 self-stretch">
        <div>
          <p className="mb-3 text-xs text-muted-foreground">在控制台里看起来是这样</p>
          <div className="flex items-end gap-4">
            {PREVIEWS.map((box) => (
              <span
                key={box}
                style={{ width: box, height: box }}
                className="relative shrink-0 overflow-hidden rounded-full bg-muted ring-1 ring-[color-mix(in_oklch,var(--hue)_30%,transparent)]"
              >
                <img src={src} alt="" draggable={false} style={placed(size, crop, box)} className="absolute max-w-none" />
              </span>
            ))}
          </div>
        </div>
        <div className="mt-auto flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={working}
            onClick={() => void confirm()}
            className={cn('bg-[var(--hue)] text-white hover:bg-[color-mix(in_srgb,var(--hue)_88%,black)]')}
          >
            {working && <LoaderCircle className="size-3.5 animate-spin" />}
            用这张
          </Button>
          <Button size="sm" variant="outline" disabled={working} onClick={onPickAnother}>
            换一张
          </Button>
          <Button size="sm" variant="ghost" disabled={working} onClick={onCancel}>
            取消
          </Button>
        </div>
      </div>
    </div>
  )
}
