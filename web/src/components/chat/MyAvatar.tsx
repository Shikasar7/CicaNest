import { useRef, useState } from 'react'
import { ImageUp, Trash2, X } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { AvatarCropper } from '@/components/bots/AvatarCropper'
import { avatarPath, sendBlob, sendJson, type AvatarView } from '@/lib/api'
import { useAvatarUrl, useLive } from '@/lib/live'
import { readPicture } from '@/lib/pictures'

/** Your own picture (聊天窗口里你的头像): nest keeps it under the id "me", beside the bots' pictures. */
export const ME = 'me'

/** Your picture, or 「我」 on a disc when you have not given one. */
export function MeGlyph({ size = 'chat' }: { size?: 'sm' | 'md' | 'chat' | 'list' | 'lg' }) {
  return <BotGlyph name="我" id={ME} size={size} />
}

/** Choose, crop and upload your picture, or go back to 「我」. */
export function MyAvatar({ onClose }: { onClose: () => void }) {
  const { setAvatar } = useLive()
  const current = useAvatarUrl(ME)
  const [cropping, setCropping] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const path = `${avatarPath(ME)}/custom`

  const pick = async (file: File | undefined) => {
    if (!file) return
    setError('')
    try {
      setCropping(await readPicture(file))
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const run = async (action: () => Promise<AvatarView>) => {
    setBusy(true)
    setError('')
    try {
      setAvatar(ME, await action())
      setCropping(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b px-5 py-4">
        <h2 className="text-base font-semibold">我的头像</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="ml-auto grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      <div className="flex flex-1 flex-col items-center gap-4 overflow-y-auto p-8 text-center">
        {cropping ? (
          <AvatarCropper
            src={cropping}
            busy={busy}
            onCancel={() => setCropping(null)}
            onPickAnother={() => input.current?.click()}
            onConfirm={(picture) => void run(() => sendBlob<AvatarView>(path, picture))}
            onError={setError}
            themed
          />
        ) : (
          <>
            <MeGlyph size="lg" />
            <p className="max-w-xs text-sm text-muted-foreground">
              聊天里你说的话旁边显示这张。只在 nest 里用，不会发给伙伴们，也不会发到 QQ。
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => input.current?.click()}
                className="flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground active:scale-[0.98] disabled:opacity-50"
              >
                <ImageUp className="size-4" />
                {current ? '换一张' : '选一张图片'}
              </button>
              {current && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(() => sendJson<AvatarView>('DELETE', path))}
                  className="flex items-center gap-1.5 rounded-full border px-4 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
                >
                  <Trash2 className="size-4" />
                  不用图片了
                </button>
              )}
            </div>
          </>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <input
          ref={input}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          hidden
          onChange={(e) => {
            void pick(e.target.files?.[0])
            e.target.value = ''
          }}
        />
      </div>
    </div>
  )
}
