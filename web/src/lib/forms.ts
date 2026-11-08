import type { KeyboardEvent } from 'react'

// ---- Keyboard ----

/** The key event belongs to an IME composition (picking a Chinese candidate), not to the form. */
export function isComposing(event: KeyboardEvent): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229
}

const NOT_TEXT = new Set(['checkbox', 'radio', 'button', 'submit', 'reset', 'file', 'range', 'color'])

/**
 * onKeyDown for a control or a whole inline form: Enter in a single-line text field saves, Esc cancels, and
 * both are ignored while an IME is composing. Enter elsewhere (textarea newline, select, checkbox) is left alone.
 */
export function editKeys({ onEnter, onEscape }: { onEnter?: () => void; onEscape?: () => void }) {
  return (event: KeyboardEvent) => {
    if (isComposing(event)) return
    const target = event.target
    if (event.key === 'Enter' && onEnter && target instanceof HTMLInputElement && !NOT_TEXT.has(target.type)) {
      event.preventDefault()
      onEnter()
    } else if (event.key === 'Escape' && onEscape) {
      event.preventDefault()
      onEscape()
    }
  }
}

// ---- Numbers (typed into <input type="text" inputMode="numeric|decimal">) ----

/** null = left empty (not set); NaN never escapes: an unparsable value is `{ ok: false }`. */
export type ParsedNumber = { ok: true; value: number | null } | { ok: false }

/** Trimmed empty → not set; otherwise `Number(text)`, which must be finite and, when given, within the bounds. */
export function parseNumber(
  text: string,
  { min, max, integer = false }: { min?: number; max?: number; integer?: boolean } = {},
): ParsedNumber {
  const trimmed = text.trim()
  if (trimmed === '') return { ok: true, value: null }
  const value = Number(trimmed)
  if (!Number.isFinite(value)) return { ok: false }
  if (integer && !Number.isInteger(value)) return { ok: false }
  if ((min !== undefined && value < min) || (max !== undefined && value > max)) return { ok: false }
  return { ok: true, value }
}

/** Show a stored number in a text field ('' when unset or not a number). */
export function numberText(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  if (typeof value === 'string') return value
  return ''
}

// ---- Text ----

/** Length in Unicode code points, the way the backend counts limits (an emoji is 1, not 2). */
export function charCount(text: string): number {
  return [...text].length
}

// ---- Accessibility ----

/** Join the ids that describe a control (hint, error), skipping absent ones. */
export function describedBy(...ids: (string | false | null | undefined)[]): string | undefined {
  const joined = ids.filter(Boolean).join(' ')
  return joined || undefined
}
