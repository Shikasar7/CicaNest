/** The bridge the nest desktop app (Electron) exposes; absent in a normal browser. */
export type NestDesktop = {
  isDesktop: true
  version: string
  /** The app runs against a test copy (another NEST_HOME), not the user's real nest. */
  testCopy?: boolean
  titleBarHeight: number
  captionWidth: number
  restartNest(): Promise<void>
  openLogsFolder(): Promise<void>
  openDataFolder(): Promise<void>
  getAutostart(): Promise<boolean>
  setAutostart(enabled: boolean): Promise<void>
  quit(options: { stopNest: boolean }): Promise<void>
}

declare global {
  interface Window {
    nestDesktop?: NestDesktop
  }
}

export const desktop: NestDesktop | undefined = typeof window === 'undefined' ? undefined : window.nestDesktop
