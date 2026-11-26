import { useMemo, useState } from 'react'

import type { GameView } from '@/lib/api'
import { cn } from '@/lib/utils'

/** The boards of 小游戏: 五子棋 on a biscuit-coloured board, 黑白棋 on a soft matcha one. The stones are the
 * same round, shiny ones; the newest has a little coral dot. */

type Point = [number, number]

/** The gradients and shadow the stones use (once per board). */
function StoneDefs() {
  return (
    <defs>
      <radialGradient id="stone-1" cx="35%" cy="30%" r="75%">
        <stop offset="0%" stopColor="#6b5a57" />
        <stop offset="55%" stopColor="#2f2626" />
        <stop offset="100%" stopColor="#191313" />
      </radialGradient>
      <radialGradient id="stone-2" cx="35%" cy="30%" r="75%">
        <stop offset="0%" stopColor="#ffffff" />
        <stop offset="60%" stopColor="#fbf5ea" />
        <stop offset="100%" stopColor="#e4d7c2" />
      </radialGradient>
      <filter id="stone-shadow" x="-30%" y="-30%" width="160%" height="170%">
        <feDropShadow dx="0" dy="2" stdDeviation="1.6" floodColor="#2f2410" floodOpacity="0.35" />
      </filter>
    </defs>
  )
}

function Stone({
  cx,
  cy,
  r,
  color,
  className,
  faint = false,
}: {
  cx: number
  cy: number
  r: number
  color: 1 | 2
  className?: string
  faint?: boolean
}) {
  const k = r / 17
  return (
    <g className={className} opacity={faint ? 0.45 : 1}>
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill={`url(#stone-${color})`}
        filter={faint ? undefined : 'url(#stone-shadow)'}
        stroke={color === 1 ? 'rgb(255 255 255 / 0.16)' : 'none'}
        strokeWidth={1.2}
      />
      {/* A soft shine at the top left: a round, squishy stone. */}
      <ellipse
        cx={cx - 5.5 * k}
        cy={cy - 6.5 * k}
        rx={6 * k}
        ry={3.6 * k}
        fill="#fff"
        opacity={color === 1 ? 0.22 : 0.85}
        transform={`rotate(-30 ${cx - 5.5 * k} ${cy - 6.5 * k})`}
      />
    </g>
  )
}

const coarsePointer = () => window.matchMedia('(pointer: coarse)').matches

/** Where the pointer is, as a point of the board (or null): `at(px, py)` maps the board's own units. */
function pointer(
  e: React.PointerEvent<SVGSVGElement> | React.MouseEvent<SVGSVGElement>,
  box: number,
  at: (px: number, py: number) => Point | null,
) {
  const rect = e.currentTarget.getBoundingClientRect()
  const scale = box / rect.width
  return at((e.clientX - rect.left) * scale, (e.clientY - rect.top) * scale)
}

/** On a touch screen the first tap only aims (a stone shows faintly); a second tap there plays. */
function useAim(count: number, canPlay: boolean) {
  const [aimed, setAimed] = useState<{ at: Point; count: number } | null>(null)
  const coarse = useMemo(() => coarsePointer(), [])
  const aim = aimed && canPlay && aimed.count === count ? aimed.at : null
  /** True when this tap should play. */
  const tap = (at: Point) => {
    if (coarse && (!aim || aim[0] !== at[0] || aim[1] !== at[1])) {
      setAimed({ at, count })
      return false
    }
    setAimed(null)
    return true
  }
  return { coarse, aim, tap }
}

const CELL = 40
const PAD = 26

export function GomokuBoard({
  game,
  side,
  canPlay,
  onPlay,
}: {
  game: GameView
  side: number
  canPlay: boolean
  onPlay: (x: number, y: number) => void
}) {
  const n = game.size ?? 15
  const moves = useMemo(() => game.moves ?? [], [game.moves])
  const win = game.win ?? []
  const span = CELL * (n - 1)
  const box = span + PAD * 2
  const yours: 1 | 2 = game.her_color === 1 ? 2 : 1
  const taken = useMemo(() => new Set(moves.map(([x, y]) => `${x},${y}`)), [moves])
  const [hover, setHover] = useState<Point | null>(null)
  const { coarse, aim, tap } = useAim(moves.length, canPlay)
  const at = (px: number, py: number): Point | null => {
    const x = Math.round((px - PAD) / CELL)
    const y = Math.round((py - PAD) / CELL)
    return x >= 0 && x < n && y >= 0 && y < n && !taken.has(`${x},${y}`) ? [x, y] : null
  }
  const last = moves.length - 1
  const winning = new Set(win.map(([x, y]) => `${x},${y}`))
  const stars = n === 15 ? [[3, 3], [11, 3], [7, 7], [3, 11], [11, 11]] : []
  const ghost = canPlay ? (coarse ? aim : hover) : null
  const ends = win.length >= 2 ? [win[0], win[win.length - 1]] : null
  const c = (i: number) => PAD + i * CELL
  return (
    <div
      className="rounded-[28px] p-[3%] shadow-[0_10px_30px_-12px_rgb(120_80_40/0.45),inset_0_1px_0_rgb(255_255_255/0.6)] ring-1 ring-black/5 [--board-a:#f8e9cc] [--board-b:#efd5a6] [--board-line:#a67a4f] dark:shadow-[0_10px_30px_-12px_rgb(0_0_0/0.7)] dark:ring-white/10 dark:[--board-a:#4b3b2c] dark:[--board-b:#3c2f23] dark:[--board-line:#c9a37b]"
      style={{ width: side, height: side, background: 'linear-gradient(145deg, var(--board-a), var(--board-b))' }}
    >
      <svg
        viewBox={`0 0 ${box} ${box}`}
        className={cn('size-full touch-manipulation select-none', canPlay ? 'cursor-pointer' : 'cursor-default')}
        role="grid"
        aria-label={`五子棋棋盘，已经下了 ${moves.length} 手`}
        onPointerMove={(e) => e.pointerType === 'mouse' && setHover(pointer(e, box, at))}
        onPointerLeave={() => setHover(null)}
        onClick={(e) => {
          if (!canPlay) return
          const point = pointer(e, box, at)
          if (!point || !tap(point)) return
          setHover(null)
          onPlay(point[0], point[1])
        }}
      >
        <StoneDefs />
        {Array.from({ length: n }, (_, i) => (
          <g key={i} stroke="var(--board-line)" strokeWidth={i === 0 || i === n - 1 ? 2.2 : 1.3} strokeLinecap="round" opacity={0.75}>
            <line x1={PAD} y1={c(i)} x2={PAD + span} y2={c(i)} />
            <line x1={c(i)} y1={PAD} x2={c(i)} y2={PAD + span} />
          </g>
        ))}
        {stars.map(([x, y]) => (
          // The star points: little hearts.
          <path
            key={`${x},${y}`}
            d="M0 3.2C-1.6 1.8-5 -0.4-5 -2.6c0-1.6 1.2-2.6 2.5-2.6 1.1 0 2 .6 2.5 1.5.5-.9 1.4-1.5 2.5-1.5 1.3 0 2.5 1 2.5 2.6 0 2.2-3.4 4.4-5 5.8z"
            transform={`translate(${c(x)} ${c(y) + 0.6}) scale(1.05)`}
            fill="var(--board-line)"
            opacity={0.75}
          />
        ))}
        {ends && (
          <line
            x1={c(ends[0][0])}
            y1={c(ends[0][1])}
            x2={c(ends[1][0])}
            y2={c(ends[1][1])}
            stroke="var(--primary)"
            strokeWidth={12}
            strokeLinecap="round"
            opacity={0.28}
          />
        )}
        {moves.map(([x, y], i) => (
          <Stone key={`${i}:${x},${y}`} cx={c(x)} cy={c(y)} r={17} color={i % 2 === 0 ? 1 : 2} className={cn(i === last && 'stone-drop')} />
        ))}
        {win.map(([x, y]) => (
          <circle key={`w${x},${y}`} className="stone-glow" cx={c(x)} cy={c(y)} r={20} fill="none" stroke="var(--primary)" strokeWidth={3} />
        ))}
        {last >= 0 && !winning.has(`${moves[last][0]},${moves[last][1]}`) && (
          // The newest stone: a little coral dot (seen on both colours, in both themes).
          <circle cx={c(moves[last][0])} cy={c(moves[last][1])} r={4.5} fill="#f07a62" />
        )}
        {ghost && (
          <g pointerEvents="none">
            <Stone cx={c(ghost[0])} cy={c(ghost[1])} r={17} color={yours} faint />
            {coarse && <circle cx={c(ghost[0])} cy={c(ghost[1])} r={20} fill="none" stroke="var(--primary)" strokeWidth={2.5} strokeDasharray="5 4" />}
          </g>
        )}
      </svg>
    </div>
  )
}

const SQ = 50
const EDGE = 14

export function ReversiBoard({
  game,
  side,
  canPlay,
  onPlay,
}: {
  game: GameView
  side: number
  canPlay: boolean
  onPlay: (x: number, y: number) => void
}) {
  const n = game.size ?? 8
  const board = useMemo(() => game.board ?? [], [game.board])
  const moves = game.moves ?? []
  const box = SQ * n + EDGE * 2
  const yours: 1 | 2 = game.her_color === 1 ? 2 : 1
  const options = useMemo(() => new Set((game.options ?? []).map(([x, y]) => `${x},${y}`)), [game.options])
  // Discs whose colour changed since the board shown before: they turn over.
  const [seen, setSeen] = useState({ id: game.id, board, turned: new Set<string>() })
  if (seen.board !== board) {
    const was = seen.id === game.id ? seen.board : null
    const out = new Set<string>()
    if (was) board.forEach((row, y) => row.forEach((v, x) => v && was[y]?.[x] && was[y][x] !== v && out.add(`${x},${y}`)))
    setSeen({ id: game.id, board, turned: out })
  }
  const turned = seen.board === board ? seen.turned : new Set<string>()
  const [hover, setHover] = useState<Point | null>(null)
  const { coarse, aim, tap } = useAim(moves.length, canPlay)
  const at = (px: number, py: number): Point | null => {
    const x = Math.floor((px - EDGE) / SQ)
    const y = Math.floor((py - EDGE) / SQ)
    return options.has(`${x},${y}`) ? [x, y] : null
  }
  const last = [...moves].reverse().find(([x]) => x >= 0)
  const ghost = canPlay ? (coarse ? aim : hover) : null
  const c = (i: number) => EDGE + i * SQ + SQ / 2
  return (
    <div
      className="rounded-[28px] p-[2.5%] shadow-[0_10px_30px_-12px_rgb(40_90_60/0.45),inset_0_1px_0_rgb(255_255_255/0.5)] ring-1 ring-black/5 [--felt-a:#cfe8c8] [--felt-b:#b4d9ad] [--cell:#e4f3df] [--cell-line:#8fbf86] dark:shadow-[0_10px_30px_-12px_rgb(0_0_0/0.7)] dark:ring-white/10 dark:[--felt-a:#2f4a35] dark:[--felt-b:#263d2c] dark:[--cell:#355440] dark:[--cell-line:#4f7a5a]"
      style={{ width: side, height: side, background: 'linear-gradient(145deg, var(--felt-a), var(--felt-b))' }}
    >
      <svg
        viewBox={`0 0 ${box} ${box}`}
        className={cn('size-full touch-manipulation select-none', canPlay ? 'cursor-pointer' : 'cursor-default')}
        role="grid"
        aria-label={`黑白棋棋盘，黑 ${game.score?.['1'] ?? 0} 子，白 ${game.score?.['2'] ?? 0} 子`}
        onPointerMove={(e) => e.pointerType === 'mouse' && setHover(pointer(e, box, at))}
        onPointerLeave={() => setHover(null)}
        onClick={(e) => {
          if (!canPlay) return
          const point = pointer(e, box, at)
          if (!point || !tap(point)) return
          setHover(null)
          onPlay(point[0], point[1])
        }}
      >
        <StoneDefs />
        {Array.from({ length: n * n }, (_, i) => {
          const x = i % n
          const y = Math.floor(i / n)
          return (
            <rect
              key={i}
              x={EDGE + x * SQ + 2}
              y={EDGE + y * SQ + 2}
              width={SQ - 4}
              height={SQ - 4}
              rx={9}
              fill="var(--cell)"
              stroke="var(--cell-line)"
              strokeOpacity={0.5}
            />
          )
        })}
        {board.map((row, y) =>
          row.map((v, x) =>
            v ? (
              <Stone
                key={`${x},${y}:${v}`}
                cx={c(x)}
                cy={c(y)}
                r={19}
                color={v as 1 | 2}
                className={cn(turned.has(`${x},${y}`) ? 'disc-flip' : last && last[0] === x && last[1] === y && 'stone-drop')}
              />
            ) : null,
          ),
        )}
        {canPlay &&
          [...options].map((key) => {
            const [x, y] = key.split(',').map(Number)
            return <circle key={`o${key}`} cx={c(x)} cy={c(y)} r={5} fill="var(--cell-line)" opacity={0.7} pointerEvents="none" />
          })}
        {last && <circle cx={c(last[0])} cy={c(last[1])} r={5} fill="#f07a62" pointerEvents="none" />}
        {ghost && (
          <g pointerEvents="none">
            <Stone cx={c(ghost[0])} cy={c(ghost[1])} r={19} color={yours} faint />
            {coarse && <circle cx={c(ghost[0])} cy={c(ghost[1])} r={22} fill="none" stroke="var(--primary)" strokeWidth={2.5} strokeDasharray="5 4" />}
          </g>
        )}
      </svg>
    </div>
  )
}
