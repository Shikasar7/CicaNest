import { useEffect, useState, type ComponentType } from 'react'
import {
  IdCard,
  MessageSquareQuote,
  BookOpen,
  ChartColumn,
  Cpu,
  House,
  Library,
  MessagesSquare,
  NotebookPen,
  AudioLines,
  Monitor,
  Moon,
  Pause,
  Radar,
  Server,
  Smile,
  SquareUserRound,
  Sun,
} from 'lucide-react'

import { NestMark } from '@/components/NestMark'
import { Segmented, type SegmentedOption } from '@/components/Segmented'
import { SignIn } from '@/components/SignIn'
import { StatusDot, type Tone } from '@/components/StatusDot'
import { sendJson, type Bot } from '@/lib/api'
import { desktop } from '@/lib/desktop'
import { cn } from '@/lib/utils'
import { LiveProvider, useBots, useLive, type LiveState } from '@/lib/live'
import { setTheme, useTheme, type ThemeChoice } from '@/lib/theme'
import { UnsavedProvider, useConfirmLeave } from '@/lib/unsaved'
import { Bots } from '@/pages/Bots'
import { Knowledge } from '@/pages/Knowledge'
import { Messages } from '@/pages/Messages'
import { Models } from '@/pages/Models'
import { Overview } from '@/pages/Overview'
import { Personas } from '@/pages/Personas'
import { Issues } from '@/pages/Issues'
import { Slang } from '@/pages/Slang'
import { Speech } from '@/pages/Speech'
import { Stickers } from '@/pages/Stickers'
import { System } from '@/pages/System'
import { Tones } from '@/pages/Tones'
import { Usage } from '@/pages/Usage'
import { Watches } from '@/pages/Watches'

type PageDef = { hash: string; label: string; Icon: ComponentType<{ className?: string }>; Page: ComponentType }

const PAGES: PageDef[] = [
  { hash: '#/', label: '总览', Icon: House, Page: Overview },
  { hash: '#/messages', label: '消息', Icon: MessagesSquare, Page: Messages },
  { hash: '#/bots', label: 'Bot', Icon: SquareUserRound, Page: Bots },
  { hash: '#/personas', label: '人设', Icon: IdCard, Page: Personas },
  { hash: '#/tones', label: '语气', Icon: MessageSquareQuote, Page: Tones },
  { hash: '#/models', label: '模型', Icon: Cpu, Page: Models },
  { hash: '#/usage', label: '用量', Icon: ChartColumn, Page: Usage },
  { hash: '#/watches', label: '监控', Icon: Radar, Page: Watches },
  { hash: '#/stickers', label: '表情包', Icon: Smile, Page: Stickers },
  { hash: '#/speech', label: '语音', Icon: AudioLines, Page: Speech },
  { hash: '#/slang', label: '黑话', Icon: BookOpen, Page: Slang },
  { hash: '#/knowledge', label: '资料', Icon: Library, Page: Knowledge },
  { hash: '#/issues', label: '问题记录', Icon: NotebookPen, Page: Issues },
  { hash: '#/system', label: '系统', Icon: Server, Page: System },
]

function useHashPage(): PageDef {
  const read = () => PAGES.find((p) => p.hash === window.location.hash) ?? PAGES[0]
  const [page, setPage] = useState<PageDef>(read)
  useEffect(() => {
    const update = () => setPage(read())
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  return page
}

const LIVE: Record<LiveState, { text: string; tone: Tone }> = {
  connecting: { text: '正在连接后台', tone: 'wait' },
  open: { text: '实时更新中', tone: 'ok' },
  login: { text: '未登录', tone: 'bad' },
  down: { text: '连不上后台', tone: 'bad' },
}

function LiveIndicator() {
  const { state } = useLive()
  return (
    <span className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
      <StatusDot tone={LIVE[state].tone} />
      {LIVE[state].text}
    </span>
  )
}

function Problem() {
  const { state } = useLive()
  if (desktop?.testCopy) {
    return (
      <p role="status" className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
        这是开发用的测试副本，账号和数据都是假的，不是你正在运行的 nest。你的 bot 不受影响，可以直接关掉这个窗口。
      </p>
    )
  }
  if (state === 'login') {
    return (
      <p className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
        {desktop ? '登录已失效。请在右下角的 nest 图标菜单里选「重启 nest」。' : '登录已失效或还没登录。请双击项目目录里的 启动.bat 重新打开控制台。'}
      </p>
    )
  }
  if (state === 'down') {
    return (
      <p className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
        连不上 nest 后台。{desktop ? '请在右下角的 nest 图标菜单里选「重启 nest」。' : '请双击 启动.bat 启动它，'}或查看 data/logs/nest.log。
      </p>
    )
  }
  return null
}

const THEMES: SegmentedOption<ThemeChoice>[] = [
  { value: 'system', label: '跟随系统', Icon: Monitor },
  { value: 'light', label: '浅色', Icon: Sun },
  { value: 'dark', label: '深色', Icon: Moon },
]

function ThemeSwitch() {
  return <Segmented options={THEMES} value={useTheme()} onChange={setTheme} label="颜色" iconOnly />
}

/** Emergency stop, reachable from every page. No undo on purpose: bots are resumed one by one on the Bot page. */
function PauseAll({ compact = false }: { compact?: boolean }) {
  const { data: bots } = useBots<Bot[]>('/api/bots')
  const [state, setState] = useState<'idle' | 'busy' | string>('idle')
  // Not knowing the bots (not loaded, or the fetch failed) must not hide the emergency stop.
  if (bots !== null && bots.every((b) => b.mode === 'paused')) return null
  const pause = async () => {
    if (!window.confirm('暂停全部 bot？暂停后不再判断、不调用模型、也不会发言。恢复要在 Bot 页逐个打开。')) return
    setState('busy')
    try {
      await sendJson('POST', '/api/bots/pause-all')
      setState('idle')
    } catch (e) {
      setState(`暂停失败：${(e as Error).message}`)
    }
  }
  return (
    <div className="space-y-1">
      <button
        type="button"
        disabled={state === 'busy'}
        onClick={() => void pause()}
        className={cn(
          'flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium whitespace-nowrap text-destructive transition-colors',
          'hover:bg-destructive/10 active:scale-[0.98] disabled:opacity-50',
          compact && 'w-auto px-2.5 py-1.5',
        )}
      >
        <Pause className="size-4" />
        全部暂停
      </button>
      {state.startsWith('暂停失败') && <p role="alert" className="px-3 text-xs text-destructive">{state}</p>}
    </div>
  )
}

function NavLinks({ page, layout }: { page: PageDef; layout: 'column' | 'row' }) {
  const confirmLeave = useConfirmLeave()
  return (
    <nav
      aria-label="页面"
      className={cn(layout === 'column' ? 'flex flex-col gap-0.5' : 'flex min-w-0 flex-1 gap-1 overflow-x-auto')}
    >
      {PAGES.map((p) => {
        const current = p.hash === page.hash
        return (
          <a
            key={p.hash}
            href={p.hash}
            aria-current={current ? 'page' : undefined}
            onClick={(e) => {
              // Leaving a page unmounts its forms: ask first when something is unsaved.
              if (!current && !confirmLeave()) e.preventDefault()
            }}
            className={cn(
              'group flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-[background-color,color,box-shadow] duration-200',
              current
                ? 'bg-sidebar-accent font-medium text-foreground shadow-[var(--shadow-lift)]'
                : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
            )}
          >
            <p.Icon
              className={cn('size-4 transition-transform duration-200', current ? 'text-foreground' : 'group-hover:scale-110')}
            />
            {p.label}
          </a>
        )
      })}
    </nav>
  )
}

function Shell() {
  const page = useHashPage()
  const { Page } = page
  const { state } = useLive()
  // The desktop app signs itself in; a browser that is not signed in gets the password screen instead.
  if (state === 'login' && !desktop) return <SignIn />
  return (
    <div className="flex h-svh overflow-hidden">
      <aside className="hidden w-52 shrink-0 flex-col border-r border-sidebar-border bg-sidebar md:flex">
        <div className="drag-region flex h-14 items-center gap-2.5 px-4">
          <NestMark />
          <span className="text-[15px] font-semibold tracking-tight">nest</span>
        </div>
        <div className="flex-1 overflow-y-auto px-2.5 py-2">
          <NavLinks page={page} layout="column" />
        </div>
        <div className="space-y-2 border-t border-sidebar-border px-2.5 py-3">
          <PauseAll />
          <div className="flex flex-wrap items-center justify-between gap-2 px-3">
            <LiveIndicator />
            <ThemeSwitch />
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col bg-surface">
        {/* Narrow screens: brand and navigation move to a top bar. */}
        <div className="flex items-center gap-3 border-b px-4 py-2 md:hidden">
          <NestMark className="size-6" />
          <NavLinks page={page} layout="row" />
          <div className="flex shrink-0 items-center gap-2">
            <ThemeSwitch />
            <PauseAll compact />
          </div>
        </div>
        <header
          className="drag-region flex h-14 shrink-0 items-center gap-4 px-6 md:px-8"
          style={desktop ? { paddingRight: desktop.captionWidth + 16 } : undefined}
        >
          <h1 className="text-lg font-semibold tracking-tight">{page.label === 'Bot' ? 'Bot 设置' : page.label}</h1>
        </header>
        <main className="flex-1 overflow-y-auto px-4 pb-10 md:px-8">
          <div className="mx-auto flex max-w-6xl flex-col gap-6">
            <Problem />
            <div key={page.hash} className="page-enter">
              <Page />
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <LiveProvider>
      <UnsavedProvider>
        <Shell />
      </UnsavedProvider>
    </LiveProvider>
  )
}
