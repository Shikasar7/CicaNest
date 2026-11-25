import { cn } from '@/lib/utils'

/** A room of the 小窝 as a line drawing: in the theme's own colours, so it goes with any of them.
 * Behind the chat it is faint; in the 小窝 it is the room. */

const stroke = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round' } as const

function LivingRoom() {
  return (
    <g {...stroke}>
      <path d="M0 150h320" />
      <rect x="24" y="22" width="74" height="62" rx="4" />
      <path d="M61 22v62M24 53h74M18 18h86" />
      <path d="M20 18c2 24-2 44 2 66M102 18c-2 24 2 44-2 66" />
      <path d="M118 132v-30a14 14 0 0 1 14-14h92a14 14 0 0 1 14 14v30" />
      <path d="M106 112a12 12 0 0 1 24 0v12h96v-12a12 12 0 0 1 24 0v26H106z" />
      <path d="M118 138v12M238 138v12M178 88v36" />
      <path d="M278 150V66M264 66h28l-7-24h-14z" />
      <ellipse cx="178" cy="164" rx="86" ry="9" />
      <rect x="140" y="30" width="34" height="26" rx="2" />
      <path d="M144 52l8-10 7 7 5-6 6 9" />
    </g>
  )
}

function Kitchen() {
  return (
    <g {...stroke}>
      <path d="M0 160h320M16 102h288M16 102v58M304 102v58M112 102v58M208 102v58" />
      <path d="M28 116h72M124 116h72M220 116h72" />
      <path d="M40 44h116M40 44v-8M156 44v-8" />
      <path d="M52 44v-14h16v14M78 44c-4-10 0-18 8-18s12 8 8 18M108 44V24h12v20M132 44v-16h14v16" />
      <path d="M220 98V80h46v18zM214 80h58M234 74c-3-6 3-8 0-14M250 74c-3-6 3-8 0-14" />
      <path d="M40 102V86h28v16M74 102c0-14 22-14 22 0" />
      <path d="M176 18v20M168 38h16l6 12h-28z" />
      <circle cx="146" cy="134" r="4" />
      <circle cx="162" cy="134" r="4" />
      <path d="M96 152h32M100 152l-4 24M124 152l4 24M98 166h28M174 152h32M178 152l-4 24M202 152l4 24M176 166h28" />
    </g>
  )
}

function Study() {
  return (
    <g {...stroke}>
      <path d="M0 160h320M20 16h92v144M20 16v144M20 52h92M20 88h92M20 124h92" />
      <path d="M28 52V26M36 52V30M44 52V24M54 52l8-24M84 52V32h16v20" />
      <path d="M30 88V64M38 88V60M46 88V66M70 88V62M78 88V66M86 88V60M96 88l6-24" />
      <path d="M32 124v-22h20v22M66 124V98M74 124v-24M82 124v-28M92 124c0-12 12-12 12 0" />
      <path d="M140 112h156M152 112v48M284 112v48M232 112v26h52" />
      <path d="M176 112V96h44v16M170 96h56" />
      <path d="M256 112V92M246 92h22l-6-18h-10zM262 74c0-8 10-12 16-6" />
      <path d="M196 24h52v34h-52zM204 50l12-14 10 10 6-6 10 10" />
      <path d="M188 148c0-10 8-16 18-16s18 6 18 16M206 148v12" />
      <ellipse cx="62" cy="171" rx="30" ry="6" />
    </g>
  )
}

function Balcony({ night }: { night?: boolean }) {
  return (
    <g {...stroke}>
      <path d="M0 160h320M0 96h320M0 104h320" />
      <path d="M20 104v56M50 104v56M80 104v56M110 104v56M140 104v56M170 104v56M200 104v56M230 104v56M260 104v56M290 104v56" />
      {night ? (
        <path d="M256 18a20 20 0 1 0 16 32 24 24 0 0 1-16-32zM206 24v6M203 27h6M292 62v6M289 65h6M216 60v4M214 62h4" />
      ) : (
        <>
          <circle cx="252" cy="36" r="16" />
          <path d="M252 10v-6M252 68v-6M226 36h-6M284 36h-6M234 18l-4-4M274 58l-4-4M234 54l-4 4M274 14l-4 4" />
        </>
      )}
      <path d="M196 148h92M196 156h92M204 156v18M280 156v18M196 148v-4h92v4" />
      <path d="M40 60c8-10 22-10 28 0 10-4 20 2 20 12H34c-6 0-4-12 6-12z" />
      <path d="M128 44c6-8 18-8 22 0 8-2 16 2 16 10h-42c-6 0-4-10 4-10z" />
      <path d="M26 96V80h28v16M40 80c-10-6-12-18-4-24M40 80c8-8 18-8 20-18M40 80V60" />
      <path d="M180 96V82h24v14M192 82c-8-4-10-14-2-18 8 4 8 14 2 18zM192 82V66" />
      <path d="M96 160c4-20 34-20 38 0M92 138h46" />
    </g>
  )
}

function Bedroom() {
  return (
    <g {...stroke}>
      <path d="M0 160h320M30 160V70M30 112h180a16 16 0 0 1 16 16v32M30 132h196" />
      <path d="M42 112c0-16 8-20 22-20h20c14 0 20 8 20 20M104 112c20-18 70-18 106 0" />
      <path d="M250 160v-38h48v38M250 136h48M274 122v-14M262 108h24l-6-18h-12z" />
      <rect x="196" y="18" width="62" height="52" rx="4" />
      <path d="M236 30a13 13 0 1 0 10 22 16 16 0 0 1-10-22z" />
      <path d="M208 30v4M206 32h4M214 56v4M212 58h4" />
      <path d="M96 40h60v10H96zM104 40V26M112 40V22M120 40V28M136 40c0-8 12-8 12 0" />
    </g>
  )
}

function Stairs() {
  return (
    <g {...stroke}>
      <path d="M0 160h320M110 160v-22h26v-22h26V94h26V72h26V50h26V28" />
      <path d="M110 132V96M240 24v-16M110 96 240-12M136 110V76M162 88V54M188 66V32M214 44V10" />
      <rect x="112" y="18" width="30" height="36" rx="2" />
      <path d="M118 46l8-12 6 7 4-5 4 10" />
    </g>
  )
}

const SCENES: Record<string, (props: { night?: boolean }) => React.JSX.Element> = {
  客厅: LivingRoom,
  厨房: Kitchen,
  书房: Study,
  阳台: Balcony,
  卧室: Bedroom,
  楼梯: Stairs,
}

export function RoomScene({ room, night, className }: { room: string; night?: boolean; className?: string }) {
  const Scene = SCENES[room.endsWith('卧室') ? '卧室' : room]
  if (!Scene) return null
  return (
    <svg
      aria-hidden
      viewBox="0 0 320 180"
      preserveAspectRatio="xMidYMax slice"
      className={cn('pointer-events-none absolute inset-0 size-full', className)}
    >
      <Scene night={night} />
    </svg>
  )
}
