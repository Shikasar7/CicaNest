/** A companion's desktop pet (its default skin): sheets of frames side by side, as data URLs. */
export type PetSprite = {
  rendering: 'smooth' | 'pixel'
  frame: number
  baseline: number
  figure: number
  actions: Record<string, { src: string; frames: number; fps: number; loop: boolean }>
  /** What she says when poked. */
  poked: string[]
}

/** A newer version of the desktop app (and the nest it brings), as the app knows about it. */
export type DesktopUpdate = {
  current: string
  state: 'idle' | 'checking' | 'latest' | 'available' | 'downloading' | 'ready' | 'installing'
  /** The newer one; '' when none is known. */
  version: string
  /** Plain text. */
  notes: string
  /** Its page on GitHub; '' when unknown. */
  page: string
  size: number
  percent: number
  error: string
  /** When it was last looked for (ms); 0 not since the app started. */
  checkedAt: number
  /** Looks by itself. */
  auto: boolean
  /** "page": this copy was not installed, only the link helps. */
  how: 'silent' | 'everyone' | 'window' | 'page'
  /** The version of the nest elsewhere this app is connected to; '' when it is the app's own, or unknown. */
  otherVersion: string
}

/** The bridge the nest desktop app (Electron) exposes; absent in a normal browser. */
export type NestDesktop = {
  isDesktop: true
  version: string
  /** The app runs against a test copy (another NEST_HOME), not the user's real nest. */
  testCopy?: boolean
  /** nest runs on another machine: the console signs in with the password; nest is not restarted from here.
   * Absent on an app from before remote mode. */
  remote?: boolean
  /** "chat": the app's chat window, drawn without the console's navigation. */
  view?: 'console' | 'chat'
  titleBarHeight: number
  captionWidth: number
  restartNest(): Promise<void>
  openLogsFolder(): Promise<void>
  openDataFolder(): Promise<void>
  getAutostart(): Promise<boolean>
  setAutostart(enabled: boolean): Promise<void>
  quit(options: { stopNest: boolean }): Promise<void>
  /** Updates: null when this app cannot be updated. All absent on an app from before them. */
  getUpdate?(): Promise<DesktopUpdate | null>
  checkUpdate?(): Promise<DesktopUpdate | null>
  /** Downloads it and checks it, then asks (in a window of the app) whether to install it now. */
  downloadUpdate?(): Promise<DesktopUpdate | null>
  installUpdate?(): Promise<DesktopUpdate | null>
  openUpdatePage?(): Promise<void>
  setUpdateAuto?(on: boolean): Promise<DesktopUpdate | null>
  /** Something about it changed; returns the unsubscribe. */
  onUpdate?(callback: () => void): () => void
  /** A Windows notification; clicking it opens this bot's chat. */
  notify?(note: { title: string; body: string; botId: string }): Promise<boolean>
  openChat?(): Promise<boolean>
  /** Show the console window (the chat window's 「控制台」 button). Absent on an app from before it. */
  openConsole?(): Promise<boolean>
  /** The 电脑上做事 settings window (authorized folders, 改不用问). Absent on an app from before it. */
  openExecutor?(): Promise<boolean>
  /** Light or dark, and the colours of the theme picked in nest (null: nest's own look), for the app's own
   * windows and its title bar. Absent on an app from before themes. */
  setTheme?(theme: {
    mode: 'system' | 'light' | 'dark'
    colors: { light: Record<string, string>; dark: Record<string, string> } | null
  }): Promise<boolean>
  /** Her desktop pet's pictures (null: she has none). Absent on an app from before it. */
  petSprite?(botId: string): Promise<PetSprite | null>
  /** A notification was clicked: show this bot ('@home': the 小窝). Returns the unsubscribe. */
  onOpenBot?(callback: (botId: string) => void): () => void
}

declare global {
  interface Window {
    nestDesktop?: NestDesktop
  }
}

export const desktop: NestDesktop | undefined = typeof window === 'undefined' ? undefined : window.nestDesktop

/** The chat window of the desktop app (or a browser opened at ?view=chat): only the chat, full height. */
export const chatView: boolean =
  typeof window !== 'undefined' &&
  (desktop?.view === 'chat' || new URLSearchParams(window.location.search).get('view') === 'chat')

/** From the chat window to the console: the app's console window, or (in a browser) a new tab. */
export function openConsole() {
  if (desktop?.openConsole) void desktop.openConsole()
  else if (desktop) window.location.assign('/') // an app from before the button: this window turns into the console
  else window.open('/', '_blank', 'noopener')
}

/** From the console to the chat on its own: the app's chat window, or (in a browser) a new tab. */
export function openChatWindow() {
  if (desktop?.openChat) void desktop.openChat()
  else window.open('/?view=chat', '_blank', 'noopener')
}
