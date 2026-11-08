import { createContext, useCallback, useContext, useEffect, useRef, type ReactNode } from 'react'

export const LEAVE_PROMPT = '有还没保存的修改，离开会丢掉。继续？'

type Unsaved = {
  /** Register a dirty draft; returns its unregister function. */
  register: () => () => void
  /** true when nothing is dirty or the user agrees to drop the changes. */
  confirmLeave: () => boolean
}

const UnsavedContext = createContext<Unsaved | null>(null)

/** Tracks dirty drafts across the console: warns on closing the tab and lets nav links ask before leaving. */
export function UnsavedProvider({ children }: { children: ReactNode }) {
  const dirty = useRef(new Set<symbol>())

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty.current.size === 0) return
      event.preventDefault()
      event.returnValue = '' // older browsers need it set to show their own prompt
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])

  const register = useCallback(() => {
    const token = Symbol('draft')
    dirty.current.add(token)
    return () => {
      dirty.current.delete(token)
    }
  }, [])

  const confirmLeave = useCallback(() => dirty.current.size === 0 || window.confirm(LEAVE_PROMPT), [])

  return <UnsavedContext.Provider value={{ register, confirmLeave }}>{children}</UnsavedContext.Provider>
}

function useUnsavedContext(): Unsaved {
  const unsaved = useContext(UnsavedContext)
  if (!unsaved) throw new Error('useUnsaved must be used inside <UnsavedProvider>')
  return unsaved
}

/** Mark this component's draft as unsaved while `dirty` is true (and until it unmounts). */
export function useUnsavedChanges(dirty: boolean): void {
  const { register } = useUnsavedContext()
  useEffect(() => (dirty ? register() : undefined), [dirty, register])
}

/** Ask before an action that would drop every unsaved draft (e.g. a nav link). */
export function useConfirmLeave(): () => boolean {
  return useUnsavedContext().confirmLeave
}
