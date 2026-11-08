import { useSyncExternalStore } from 'react'

/** 浅色 / 深色, or 跟随系统 (the system's light/dark setting). Remembered per browser, so the Mac and the
 * Windows PC can each keep their own. */
export type ThemeChoice = 'system' | 'light' | 'dark'

const KEY = 'nest.theme'
const systemDark = window.matchMedia('(prefers-color-scheme: dark)')
const listeners = new Set<() => void>()

const read = (): ThemeChoice => {
  try {
    const stored = window.localStorage.getItem(KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system' // storage blocked (private window, previews): follow the system
  }
}

let choice = read()

const apply = () => {
  const dark = choice === 'dark' || (choice === 'system' && systemDark.matches)
  document.documentElement.classList.toggle('dark', dark)
}

const changed = () => {
  apply()
  listeners.forEach((l) => l())
}

/** Applies the remembered choice and keeps following the system (and other tabs). Called once in main.tsx:
 * inline scripts are blocked by the CSP, so it can't run from index.html. */
export function startTheme() {
  apply()
  systemDark.addEventListener('change', apply)
  window.addEventListener('storage', (e) => {
    if (e.key !== KEY) return
    choice = read()
    changed()
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

export function useTheme(): ThemeChoice {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => choice,
  )
}
