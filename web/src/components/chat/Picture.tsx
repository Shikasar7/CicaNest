import { useRef, useState, type ReactNode } from 'react'
import { Download, X } from 'lucide-react'

/** A picture of the chat, opened over the chat and not in place of it: a link of its own would be opened by the
 * desktop app in this very window, and closing the picture would close the chat. Esc, the cross or a click
 * beside it closes it; a click on it shows it at its own size when it is larger than the window. */
export function Picture({ src, name, title, children }: { src: string; name: string; title: string; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [open, setOpen] = useState(false)
  const [full, setFull] = useState(false) // at its own size, scrolled
  const [larger, setLarger] = useState(false) // than it is shown
  const [failed, setFailed] = useState(false)
  const close = () => dialog.current?.close()
  const measure = (img: HTMLImageElement) => setLarger(img.naturalWidth > img.clientWidth || img.naturalHeight > img.clientHeight)
  const round = 'grid size-9 place-items-center rounded-full bg-black/60 text-white hover:bg-black/80'
  return (
    <>
      <button
        type="button"
        title={title}
        aria-label="看大图"
        className="cursor-zoom-in rounded-2xl"
        onClick={() => {
          setFull(false)
          setFailed(false)
          setOpen(true)
          dialog.current?.showModal()
        }}
      >
        {children}
      </button>
      <dialog
        ref={dialog}
        aria-label="图片"
        onClose={() => setOpen(false)}
        onClick={(e) => e.target === e.currentTarget && close()}
        className="m-0 h-dvh max-h-none w-dvw max-w-none bg-transparent p-0 backdrop:bg-black/70"
      >
        {open && (
          <>
            <div className="h-full w-full overflow-auto" onClick={(e) => e.target === e.currentTarget && close()}>
              <div
                className="grid min-h-full w-max min-w-full place-items-center p-12 max-sm:p-4"
                onClick={(e) => e.target === e.currentTarget && close()}
              >
                {failed ? (
                  <p className="rounded-full bg-black/60 px-4 py-2 text-sm text-white">这张图打不开了</p>
                ) : (
                  <img
                    src={src}
                    alt="图片"
                    draggable={false}
                    onLoad={(e) => measure(e.currentTarget)}
                    onError={() => setFailed(true)}
                    onClick={() => (full || larger) && setFull(!full)}
                    className={
                      full
                        ? 'max-w-none cursor-zoom-out'
                        : `max-h-[calc(100dvh-6rem)] max-w-[calc(100dvw-6rem)] rounded-lg object-contain shadow-2xl max-sm:max-w-[calc(100dvw-2rem)] ${larger ? 'cursor-zoom-in' : ''}`
                    }
                  />
                )}
              </div>
            </div>
            <div className="fixed top-3 right-3 flex gap-2">
              <a href={src} download={name} className={round} aria-label="存下来" title="存下来">
                <Download className="size-4" />
              </a>
              <button type="button" onClick={close} className={round} aria-label="关掉图片" title="关掉（Esc）">
                <X className="size-5" />
              </button>
            </div>
          </>
        )}
      </dialog>
    </>
  )
}
