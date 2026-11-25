import { useEffect, useRef, useState, type DragEvent } from 'react'

/** Files dragged in from outside (Explorer, another window). Things dragged inside the page are left alone: a
 * companion moved in the list, or a picture of this chat (the browser offers that as a file too). */

// What is sent as a picture (desk-media takes these, up to this size); every other file goes to her folder.
const PICTURES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
const PICTURE_MAX = 10 * 1024 ** 2
export const FILE_MAX = 50 * 1024 ** 2 // nest.core.workspace
export const DROP_MAX = 10 // at once

export const isPicture = (file: File) => PICTURES.includes(file.type) && file.size <= PICTURE_MAX

let inside = false // a drag that began in this page is going on

const fromOutside = (transfer: DataTransfer | null) => !inside && !!transfer?.types.includes('Files')

/** The dropped files, without folders (a folder cannot be read here), and whether there were any folders. */
function filesOf(transfer: DataTransfer): { files: File[]; folders: boolean } {
  const files: File[] = []
  let folders = false
  for (const item of transfer.items) {
    if (item.kind !== 'file') continue
    if (item.webkitGetAsEntry()?.isDirectory) {
      folders = true
      continue
    }
    const file = item.getAsFile()
    if (file) files.push(file)
  }
  return { files, folders }
}

/** On the whole chat page: a file let go anywhere else does nothing (the browser would leave the page and open
 * it instead). */
export function useDropGuard() {
  useEffect(() => {
    const begin = () => {
      inside = true
    }
    const end = () => {
      inside = false
    }
    const refuse = (e: globalThis.DragEvent) => {
      if (e.defaultPrevented || !e.dataTransfer?.types.includes('Files')) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'none'
    }
    document.addEventListener('dragstart', begin)
    document.addEventListener('dragend', end)
    document.addEventListener('mousemove', end) // no mouse moves while dragging: a drag whose end was missed
    window.addEventListener('dragover', refuse)
    window.addEventListener('drop', refuse)
    return () => {
      document.removeEventListener('dragstart', begin)
      document.removeEventListener('dragend', end)
      document.removeEventListener('mousemove', end)
      window.removeEventListener('dragover', refuse)
      window.removeEventListener('drop', refuse)
      inside = false
    }
  }, [])
}

/** Handlers for a place that takes files, and whether some are being held over it. */
export function useFileDrop(onFiles: (files: File[], folders: boolean) => void) {
  const [over, setOver] = useState(false)
  const depth = useRef(0) // entered minus left: the children of the place fire these too
  const clear = () => {
    depth.current = 0
    setOver(false)
  }
  return {
    over,
    handlers: {
      onDragEnter: (e: DragEvent) => {
        if (!fromOutside(e.dataTransfer)) return
        e.preventDefault()
        depth.current += 1
        setOver(true)
      },
      onDragOver: (e: DragEvent) => {
        if (!fromOutside(e.dataTransfer)) return
        e.preventDefault()
        e.dataTransfer.dropEffect = 'copy'
      },
      onDragLeave: (e: DragEvent) => {
        if (!fromOutside(e.dataTransfer)) return
        depth.current -= 1
        if (depth.current <= 0) clear()
      },
      onDrop: (e: DragEvent) => {
        clear()
        if (!fromOutside(e.dataTransfer)) return
        e.preventDefault()
        const { files, folders } = filesOf(e.dataTransfer)
        onFiles(files, folders)
      },
    },
  }
}
