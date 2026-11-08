import { useEffect, useRef, useState } from 'react'

import { useUnsavedChanges } from '@/lib/unsaved'

const sameJson = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

export const CHANGED_ELSEWHERE = '这里刚在别处改过，保存会覆盖那次修改'

export type Draft<T> = {
  /** What to render: the draft once it differs from where it started, else the server value. */
  value: T
  /** A draft exists (编辑 was pressed or something was changed). */
  editing: boolean
  /** The draft differs from the server value it started from. */
  dirty: boolean
  /** The server value changed since the draft started and the draft is dirty: saving overwrites that change. */
  changedElsewhere: boolean
  /** Start an untouched draft (编辑). */
  start: () => void
  /** Change the draft, starting it from the current server value on the first change. */
  set: (next: T | ((current: T) => T)) => void
  /** Drop the draft: only after a successful save, or on 取消/Esc. */
  clear: () => void
}

/**
 * Edit state for one control or form, owned by the component that renders it.
 *
 * `null` means "not editing": the server value is rendered and refetches flow through. Server values are never
 * copied into state by an effect; an untouched draft keeps following the server, a dirty one is kept as typed.
 * Dirty drafts register with the unsaved-changes guard, except while `saving`: leaving then loses nothing, since
 * the save is already on its way.
 */
export function useDraft<T>(server: T, equal: (a: T, b: T) => boolean = sameJson, saving = false): Draft<T> {
  const [state, setState] = useState<{ value: T; base: T } | null>(null)
  const dirty = state !== null && !equal(state.value, state.base)
  useUnsavedChanges(dirty && !saving)
  return {
    value: state && dirty ? state.value : server,
    editing: state !== null,
    dirty,
    changedElsewhere: state !== null && dirty && !equal(state.base, server),
    start: () => setState((current) => current ?? { value: server, base: server }),
    set: (next) =>
      setState((current) => {
        const kept = current !== null && !equal(current.value, current.base)
        const from = kept ? current : { value: server, base: server }
        const value = typeof next === 'function' ? (next as (v: T) => T)(from.value) : next
        return { value, base: from.base }
      }),
    clear: () => setState(null),
  }
}

/**
 * Focus handling around an inline editor: focus the first field when editing starts, and give focus back to
 * the 编辑 button when it ends (保存 or 取消).
 */
export function useEditFocus<F extends HTMLElement = HTMLElement, B extends HTMLElement = HTMLButtonElement>(
  editing: boolean,
) {
  const firstField = useRef<F>(null)
  const editButton = useRef<B>(null)
  const was = useRef(editing)
  useEffect(() => {
    if (editing && !was.current) firstField.current?.focus()
    if (!editing && was.current) editButton.current?.focus()
    was.current = editing
  }, [editing])
  return { firstField, editButton }
}
