import { useSyncExternalStore } from 'react'

import { desktop } from '@/lib/desktop'

/** 浅色 / 深色, or 跟随系统 (the system's light/dark setting). Remembered per browser, so the computer and the
 * Windows PC can each keep their own. */
export type ThemeChoice = 'system' | 'light' | 'dark'

/** One look's colours, by the names nest.core.themes uses (accent, background, text…). */
export type Colors = Record<string, string>
export type Palette = { light: Colors; dark: Colors }

/** GET /api/appearance: the theme and accent colour picked in nest (the same on every screen), what there
 * is to pick from, and every colour of the picked one. */
export type Appearance = {
  theme: string
  accent: string | null
  /** The picked theme's plugin was switched off or removed: nest's own look is shown. */
  missing: boolean
  /** nest's own look: nothing to lay over the stylesheet. */
  plain: boolean
  themes: ThemeEntry[]
  colors: Palette
}

export type ThemeEntry = {
  id: string
  name: string
  description: string
  author: string
  /** The plugin it came with; null for nest's own. */
  plugin: string | null
  swatch: { light: Colors; dark: Colors }
}

const KEY = 'nest.theme'
const COLORS_KEY = 'nest.colors' // the last colours nest gave: shown at once on the next start (and signed out)
const systemDark = window.matchMedia('(prefers-color-scheme: dark)')
const listeners = new Set<() => void>()
const HEX = /^#[0-9a-f]{6}$/

const read = (): ThemeChoice => {
  try {
    const stored = window.localStorage.getItem(KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system' // storage blocked (private window, previews): follow the system
  }
}

let choice = read()
let dark = false
let palette: Palette | null = null

const apply = () => {
  dark = choice === 'dark' || (choice === 'system' && systemDark.matches)
  document.documentElement.classList.toggle('dark', dark)
}

const changed = () => {
  apply()
  listeners.forEach((l) => l())
  tellDesktop()
}

/** The desktop app draws its own windows (starting, offline, settings) and its title bar buttons: it is
 * told the light/dark choice and the colours, and remembers them for its next start. */
function tellDesktop() {
  desktop?.setTheme?.({ mode: choice, colors: palette }).catch(() => {})
}

// -- colours ------------------------------------------------------------------------------------------------

/** The stylesheet's variables (index.css) and which colour each takes. */
const VARIABLES: [string, string][] = [
  ['--background', 'background'],
  ['--foreground', 'text'],
  ['--surface', 'surface'],
  ['--card', 'card'],
  ['--card-foreground', 'text'],
  ['--popover', 'popover'],
  ['--popover-foreground', 'text'],
  ['--primary', 'accent'],
  ['--primary-foreground', 'accent_text'],
  ['--secondary', 'fill'],
  ['--secondary-foreground', 'text'],
  ['--muted', 'fill'],
  ['--muted-foreground', 'muted'],
  ['--accent', 'hover'],
  ['--accent-foreground', 'text'],
  ['--border', 'border'],
  ['--input', 'input'],
  ['--ring', 'ring'],
  ['--sidebar', 'sidebar'],
  ['--sidebar-foreground', 'text'],
  ['--sidebar-primary', 'accent'],
  ['--sidebar-primary-foreground', 'accent_text'],
  ['--sidebar-accent', 'sidebar_active'],
  ['--sidebar-accent-foreground', 'text'],
  ['--sidebar-border', 'border'],
  ['--sidebar-ring', 'ring'],
]

const good = (colors: Colors | undefined): colors is Colors =>
  !!colors && VARIABLES.every(([, slot]) => HEX.test(colors[slot] ?? ''))

/** CSS that lays a palette over index.css (both looks; the `dark` class picks one). Exported for tests. */
export function paletteCss(p: Palette): string {
  const block = (c: Colors) => VARIABLES.map(([name, slot]) => `${name}:${c[slot]};`).join('')
  // In the desktop app Mica shows through the sidebar and the page (index.css, "desktop app").
  const mica = (c: Colors, isDark: boolean) =>
    `--sidebar:color-mix(in srgb,${isDark ? c.background : c.sidebar} ${isDark ? 45 : 55}%,transparent);` +
    `--surface:color-mix(in srgb,${c.surface} ${isDark ? 80 : 82}%,transparent);`
  return (
    `html:root:not(.dark){${block(p.light)}}` +
    `html:root.dark{${block(p.dark)}}` +
    `html[data-desktop]:root:not(.dark){${mica(p.light, false)}}` +
    `html[data-desktop]:root.dark{${mica(p.dark, true)}}`
  )
}

function drawColors() {
  let style = document.getElementById('nest-theme') as HTMLStyleElement | null
  if (!palette) {
    style?.remove()
    return
  }
  if (!style) {
    style = document.createElement('style')
    style.id = 'nest-theme'
    document.head.appendChild(style)
  }
  style.textContent = paletteCss(palette)
}

/** Show what nest says (GET or PUT /api/appearance), remember it for the next start, tell the desktop app. */
export function applyAppearance(view: Pick<Appearance, 'plain' | 'colors'>) {
  const next = !view.plain && good(view.colors?.light) && good(view.colors?.dark) ? view.colors : null
  if (JSON.stringify(next) === JSON.stringify(palette)) return
  palette = next
  drawColors()
  try {
    if (palette) window.localStorage.setItem(COLORS_KEY, JSON.stringify(palette))
    else window.localStorage.removeItem(COLORS_KEY)
  } catch {
    // not remembered; shown on this page anyway
  }
  tellDesktop()
}

function readColors(): Palette | null {
  try {
    const stored = JSON.parse(window.localStorage.getItem(COLORS_KEY) ?? 'null') as Palette | null
    return stored && good(stored.light) && good(stored.dark) ? stored : null
  } catch {
    return null
  }
}

// -- light or dark --------------------------------------------------------------------------------------------

/** Applies the remembered choice and colours and keeps following the system (and other tabs). Called once in
 * main.tsx: inline scripts are blocked by the CSP, so it can't run from index.html. */
export function startTheme() {
  apply()
  palette = readColors()
  drawColors()
  tellDesktop()
  systemDark.addEventListener('change', () => {
    apply()
    listeners.forEach((l) => l())
  })
  window.addEventListener('storage', (e) => {
    if (e.key === KEY) {
      choice = read()
      changed()
    } else if (e.key === COLORS_KEY) {
      palette = readColors()
      drawColors()
    }
  })
}

export function setTheme(next: ThemeChoice) {
  choice = next
  try {
    if (next === 'system') window.localStorage.removeItem(KEY)
    else window.localStorage.setItem(KEY, next)
  } catch {
    // not remembered; this page still switches
  }
  changed()
}

const subscribe = (listener: () => void) => {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useTheme(): ThemeChoice {
  return useSyncExternalStore(subscribe, () => choice)
}

/** Whether the page is drawn dark right now (the choice, or the system's when following it). */
export function useDark(): boolean {
  return useSyncExternalStore(subscribe, () => dark)
}
