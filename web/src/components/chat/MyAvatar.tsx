import { useRef, useState } from 'react'
import { ImageUp, Trash2 } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { AvatarCropper } from '@/components/bots/AvatarCropper'
import { avatarPath, sendBlob, sendJson, type AvatarView } from '@/lib/api'
import { useAvatarUrl, useLive } from '@/lib/live'
import { readPicture } from '@/lib/pictures'

/** Your own picture (聊天窗口里你的头像): nest keeps it under the id "me", beside the bots' pictures. */
export const ME = 'me'

/** Your picture, or the one that comes with nest when you have not given one. */
export function MeGlyph({ size = 'chat' }: { size?: 'sm' | 'md' | 'chat' | 'list' | 'lg' }) {
  return <BotGlyph name="我" id={ME} size={size} />
}

/** Choose, crop and upload your picture, or go back to the one that comes with nest (in 设置). */
export function MyAvatar() {
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
    <div className="space-y-3">
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
        <div className="flex flex-wrap items-center gap-4">
          <MeGlyph size="lg" />
          <div className="flex flex-wrap gap-2">
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
                换回默认的
              </button>
            )}
          </div>
          <p className="w-full text-xs text-muted-foreground">
            聊天里你说的话旁边显示这张。只在 nest 里用，不会发给伙伴们，也不会发到别的平台。
          </p>
        </div>
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
  )
}
