import { useLayoutEffect, useRef, useState } from 'react'

import type { UsageDay } from '@/lib/api'
import { formatCount, shortDay } from '@/lib/format'

const HEIGHT = 120 // plot area
const TOP = 8
const AXIS = 20 // x-axis label band below the plot
const LEFT = 40 // y-axis labels
const RIGHT = 4
const BAR_MAX = 24
const GAP = 2 // surface gap between the ok and failed parts of a bar
const LABEL_WIDTH = 36 // room one "10-25" needs on the x axis

/** A rect whose top corners are rounded (the data end); the baseline end stays square. */
function topRounded(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h)
  return `M${x},${y + h}V${y + r}A${r},${r} 0 0 1 ${x + r},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h}Z`
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setWidth(el.clientWidth)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}

/**
 * Calls per day for one model: successful calls in the primary colour with failures stacked on top in the
 * destructive colour. Every model's chart shares `ticks`, so their heights compare directly.
 */
export function DailyChart({ label, days, ticks }: { label: string; days: UsageDay[]; ticks: number[] }) {
  const [box, width] = useWidth<HTMLDivElement>()
  const yMax = ticks[ticks.length - 1]
  const total = days.reduce((sum, d) => sum + d.calls, 0)
  const failed = days.reduce((sum, d) => sum + d.errors, 0)
  const busiest = days.reduce<UsageDay | null>((best, d) => (!best || d.calls > best.calls ? d : best), null)
  const summary =
    `${label} 每天的调用次数：${days.length} 天共 ${total} 次，失败 ${failed} 次` +
    (busiest && busiest.calls > 0 ? `，最多的一天是 ${shortDay(busiest.day)}，${busiest.calls} 次` : '')

  const plotWidth = Math.max(0, width - LEFT - RIGHT)
  const slot = days.length ? plotWidth / days.length : 0
  const barWidth = Math.max(2, Math.min(BAR_MAX, slot * 0.6))
  const y = (value: number) => TOP + HEIGHT * (1 - value / yMax)
  // Label every k-th day, counted back from today so the latest day always has one.
  const every = Math.max(1, Math.ceil(LABEL_WIDTH / Math.max(slot, 1)))

  return (
    <div ref={box} className="w-full">
      {width > 0 && (
        <svg
          width={width}
          height={TOP + HEIGHT + AXIS}
          role="img"
          aria-label={summary}
          className="block overflow-visible text-[10px]"
        >
          {ticks.map((t) => (
            <g key={t}>
              <line x1={LEFT} x2={width - RIGHT} y1={y(t)} y2={y(t)} className="stroke-border" strokeWidth={1} />
              <text
                x={LEFT - 6}
                y={y(t)}
                textAnchor="end"
                dominantBaseline="middle"
                className="fill-muted-foreground tabular-nums"
              >
                {formatCount(t)}
              </text>
            </g>
          ))}
          {days.map((d, i) => {
            const x = LEFT + slot * i + (slot - barWidth) / 2
            const ok = Math.max(0, d.calls - d.errors)
            const okTop = y(ok)
            const allTop = y(d.calls)
            const base = y(0)
            // Keep each part at least 2px tall so a single call still shows.
            const okHeight = ok > 0 ? Math.max(2, base - okTop - (d.errors > 0 ? GAP / 2 : 0)) : 0
            const errHeight = d.errors > 0 ? Math.max(2, okTop - allTop - (ok > 0 ? GAP / 2 : 0)) : 0
            const errY = ok > 0 ? base - okHeight - GAP - errHeight : base - errHeight
            const showLabel = (days.length - 1 - i) % every === 0
            return (
              <g key={d.day} className="group/bar">
                <title>{`${shortDay(d.day)}：调用 ${d.calls} 次，失败 ${d.errors} 次`}</title>
                {/* The hit area is the whole column, not just the painted bar. */}
                <rect x={LEFT + slot * i} y={TOP} width={slot} height={HEIGHT} className="fill-transparent" />
                {okHeight > 0 && (
                  <path
                    d={errHeight > 0 ? `M${x},${base}h${barWidth}v${-okHeight}h${-barWidth}Z` : topRounded(x, base - okHeight, barWidth, okHeight)}
                    className="fill-primary group-hover/bar:opacity-75"
                  />
                )}
                {errHeight > 0 && (
                  <path
                    d={topRounded(x, errY, barWidth, errHeight)}
                    className="fill-destructive group-hover/bar:opacity-75"
                  />
                )}
                {showLabel && (
                  <text
                    x={LEFT + slot * i + slot / 2}
                    y={TOP + HEIGHT + 14}
                    textAnchor="middle"
                    className="fill-muted-foreground tabular-nums"
                  >
                    {shortDay(d.day)}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
      )}
    </div>
  )
}
