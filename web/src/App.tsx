import { useEffect, useState, type ComponentType } from 'react'
import {
  AppWindow,
  AudioLines,
  BookOpen,
  Brain,
  ChartColumn,
  Cpu,
  House,
  LayoutDashboard,
  IdCard,
  Library,
  ListChecks,
  MessageCircleHeart,
  MessagesSquare,
  Monitor,
  Moon,
  NotebookPen,
  Palette,
  Pause,
  Plug,
  Puzzle,
  Radar,
  Server,
  Smile,
  SquareUserRound,
  Sun,
} from 'lucide-react'

import { AppearanceSync } from '@/components/Appearance'
import { Approvals } from '@/components/Approvals'
import { NestMark } from '@/components/NestMark'
import { Segmented, type SegmentedOption } from '@/components/Segmented'
import { SignIn } from '@/components/SignIn'
import { StatusDot, type Tone } from '@/components/StatusDot'
import { sendJson, type Bot } from '@/lib/api'
import { captionRoom, chatView, desktop, openChatWindow, openConsole } from '@/lib/desktop'
import { cn } from '@/lib/utils'
import { LiveProvider, useBots, useLive, type LiveState } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import { setTheme, useTheme, type ThemeChoice } from '@/lib/theme'
import { UnsavedProvider, useConfirmLeave } from '@/lib/unsaved'
import { Bots } from '@/pages/Bots'
import { Character } from '@/pages/Character'
import { Chat } from '@/pages/Chat'
import { Knowledge } from '@/pages/Knowledge'
import { Look } from '@/pages/Look'
import { Memories } from '@/pages/Memories'
import { Messages } from '@/pages/Messages'
import { Models } from '@/pages/Models'
import { Overview } from '@/pages/Overview'
import { Platforms } from '@/pages/Platforms'
import { Plugins } from '@/pages/Plugins'
import { Issues } from '@/pages/Issues'
import { Slang } from '@/pages/Slang'
import { Speech } from '@/pages/Speech'
import { Stickers } from '@/pages/Stickers'
import { System } from '@/pages/System'
import { TasksPage } from '@/pages/Tasks'
import { Usage } from '@/pages/Usage'
import { Watches } from '@/pages/Watches'

type PageDef = {
  hash: string
  label: string
  Icon: ComponentType<{ className?: string }>
  Page: ComponentType
  /** The sidebar heading it sits under; none for the two at the top. */
  group?: string
  /** Other addresses that open this page (an older link, a tab of it). */
  aliases?: string[]
}

/** Companions first: who they are, what they do, what happened, then the settings. QQ and the other platforms are
 * one page among the settings, not the frame of the whole app. */
const PAGES: PageDef[] = [
  { hash: '#/', label: '首页', Icon: House, Page: Overview },
  { hash: '#/chat', label: '聊天', Icon: MessageCircleHeart, Page: Chat },
  { hash: '#/bots', label: '伙伴', Icon: SquareUserRound, Page: Bots, group: '伙伴' },
  { hash: '#/personas', label: '人设和语气', Icon: IdCard, Page: Character, group: '伙伴', aliases: ['#/tones'] },
  { hash: '#/memories', label: '记忆', Icon: Brain, Page: Memories, group: '伙伴' },
  { hash: '#/stickers', label: '表情包', Icon: Smile, Page: Stickers, group: '伙伴' },
  { hash: '#/speech', label: '语音', Icon: AudioLines, Page: Speech, group: '伙伴' },
  { hash: '#/tasks', label: '任务', Icon: ListChecks, Page: TasksPage, group: '她们会做的' },
  { hash: '#/watches', label: '监控', Icon: Radar, Page: Watches, group: '她们会做的' },
  { hash: '#/knowledge', label: '资料库', Icon: Library, Page: Knowledge, group: '她们会做的' },
  { hash: '#/slang', label: '黑话', Icon: BookOpen, Page: Slang, group: '她们会做的' },
  { hash: '#/messages', label: '消息记录', Icon: MessagesSquare, Page: Messages, group: '记录' },
  { hash: '#/usage', label: '用量', Icon: ChartColumn, Page: Usage, group: '记录' },
  { hash: '#/issues', label: '问题记录', Icon: NotebookPen, Page: Issues, group: '记录' },
  { hash: '#/models', label: '模型', Icon: Cpu, Page: Models, group: '设置' },
  { hash: '#/platforms', label: '平台接入', Icon: Plug, Page: Platforms, group: '设置' },
  { hash: '#/plugins', label: '插件', Icon: Puzzle, Page: Plugins, group: '设置' },
  { hash: '#/look', label: '外观', Icon: Palette, Page: Look, group: '设置' },
  { hash: '#/system', label: '系统', Icon: Server, Page: System, group: '设置' },
]

/** Installed plugins with a newer version in the market (looked at once a day). */
function usePluginUpdates(): number {
  const { botsVersion } = useLive()
  return useLiveData<{ updates: unknown[] }>('/api/market/updates', [botsVersion]).data?.updates.length ?? 0
}

function useHashPage(): PageDef {
  const read = () => {
    // '#/chat?bot=…': the part after ? is for the page itself.
    const hash = window.location.hash.split('?')[0]
    return PAGES.find((p) => p.hash === hash || p.aliases?.includes(hash)) ?? PAGES[0]
  }
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
        {desktop && !desktop.remote ? '登录已失效。请在右下角的 nest 图标菜单里选「重启 nest」。' : '登录已失效或还没登录。请双击项目目录里的 启动.bat 重新打开控制台。'}
      </p>
    )
  }
  if (state === 'down') {
    if (desktop?.remote) {
      return (
        <p className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          连不上运行 nest 的那台电脑。它可能关机或睡着了，或者不在同一个网络里；连上后会自动恢复。
        </p>
      )
    }
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

/** Emergency stop, reachable from every page while someone speaks on a platform (a group can hear her there); a
 * nest used only on the desk has nothing to stop in a hurry. No undo on purpose: companions are resumed one by one
 * on the 伙伴 page. */
function PauseAll({ compact = false }: { compact?: boolean }) {
  const { data: bots } = useBots<Bot[]>('/api/bots')
  const [state, setState] = useState<'idle' | 'busy' | string>('idle')
  // Not knowing the bots (not loaded, or the fetch failed) must not hide the emergency stop.
  if (bots !== null && bots.every((b) => b.mode === 'paused' || b.platform === 'desk')) return null
  const pause = async () => {
    if (!window.confirm('暂停全部伙伴？暂停后她们不再判断、不调用模型，群里和桌面上都不会说话。恢复要在伙伴页逐个打开。')) return
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
  const updates = usePluginUpdates()
  const confirmLeave = useConfirmLeave()
  return (
    <nav
      aria-label="页面"
      className={cn(layout === 'column' ? 'flex flex-col gap-0.5' : 'flex min-w-0 flex-1 gap-1 overflow-x-auto')}
    >
      {PAGES.map((p, i) => {
        const current = p.hash === page.hash
        // A heading where a group starts; the narrow top bar has no room for them.
        const heading = layout === 'column' && p.group && p.group !== PAGES[i - 1]?.group && (
          <p key={`${p.group}-heading`} className="px-3 pt-3 pb-1 text-[11px] font-medium tracking-wider text-muted-foreground/80">
            {p.group}
          </p>
        )
        const link = (
          <a
            key={p.hash}
            href={p.hash}
            aria-current={current ? 'page' : undefined}
            onClick={(e) => {
              // Leaving a page unmounts its forms: ask first when something is unsaved.
              if (!current && !confirmLeave()) e.preventDefault()
            }}
            className={cn(
              'group flex shrink-0 items-center gap-2.5 rounded-lg px-3 text-sm transition-[background-color,color,box-shadow] duration-200',
              layout === 'column' ? 'py-1.5' : 'py-2',
              current
                ? 'bg-sidebar-accent font-medium text-foreground shadow-[var(--shadow-lift)]'
                : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
            )}
          >
            <p.Icon
              className={cn('size-4 transition-transform duration-200', current ? 'text-foreground' : 'group-hover:scale-110')}
            />
            {p.label}
            {p.hash === '#/plugins' && updates > 0 && (
              <span
                title={`${updates} 个插件有新版本`}
                className="ml-auto rounded-full bg-primary px-1.5 text-[10px] leading-4 text-primary-foreground"
              >
                {updates}
              </span>
            )}
          </a>
        )
        if (p.hash !== '#/chat' || layout !== 'column') return heading ? [heading, link] : link
        // Beside the link, not in it: it opens another window and leaves this page as it is.
        return (
          <div key={p.hash} className="relative grid">
            {link}
            <button
              type="button"
              onClick={openChatWindow}
              aria-label="打开聊天窗口"
              title="打开聊天窗口"
              className="absolute top-1/2 right-1.5 grid size-7 -translate-y-1/2 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <AppWindow className="size-3.5" />
            </button>
          </div>
        )
      })}
    </nav>
  )
}

/** The desktop app's chat window: the 聊天 page alone, under a draggable strip for the window's buttons. */
function ChatWindow() {
  return (
    <div className="flex h-svh flex-col overflow-hidden bg-surface">
      <header
        className="drag-region flex h-10 shrink-0 items-center gap-2 px-4"
        style={captionRoom(12)}
      >
        <NestMark className="size-5" />
        <span className="text-sm font-medium">聊天</span>
        <button
          type="button"
          onClick={openConsole}
          title="打开控制台：伙伴、模型、插件、外观这些设置都在那里"
          className="ml-auto flex h-7 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <LayoutDashboard className="size-3.5" />
          控制台
        </button>
      </header>
      <main className="flex min-h-0 flex-1 flex-col px-3 pb-3">
        <Problem />
        <Chat standalone />
      </main>
    </div>
  )
}

function Shell() {
  const page = useHashPage()
  const { Page } = page
  const { state } = useLive()
  // The desktop app signs itself in to a nest on this machine; a browser, or the app connected to a nest
  // elsewhere, gets the password screen instead.
  if (state === 'login' && (!desktop || desktop.remote)) return <SignIn />
  if (chatView) {
    return (
      <>
        <ChatWindow />
        <Approvals />
      </>
    )
  }
  return (
    <div className="flex h-svh overflow-hidden">
      <aside className="hidden w-52 shrink-0 flex-col border-r border-sidebar-border bg-sidebar md:flex">
        {/* macOS: the window's lights get a strip of their own above the brand. */}
        {desktop?.captionSide === 'left' && <div className="drag-region h-10 shrink-0" />}
        <div
          className={cn(
            'drag-region flex items-center gap-2.5 px-4',
            desktop?.captionSide === 'left' ? 'h-12 pb-1' : 'h-14',
          )}
        >
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
          style={desktop?.captionSide === 'left' ? undefined : captionRoom(16)}
        >
          <h1 className="text-lg font-semibold tracking-tight">{page.label}</h1>
          {page.hash === '#/chat' && (
            <button
              type="button"
              onClick={openChatWindow}
              className="ml-auto flex h-8 items-center gap-1.5 rounded-full border bg-card px-3.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <AppWindow className="size-4" />
              在单独的窗口里聊
            </button>
          )}
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
      <Approvals />
    </div>
  )
}

export default function App() {
  return (
    <LiveProvider>
      <AppearanceSync />
      <UnsavedProvider>
        <Shell />
      </UnsavedProvider>
    </LiveProvider>
  )
}
