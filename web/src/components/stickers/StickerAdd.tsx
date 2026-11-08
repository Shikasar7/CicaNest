import { useId, useRef, useState } from 'react'
import { ImagePlus, LoaderCircle, Palette } from 'lucide-react'

import { useAction } from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Textarea } from '@/components/ui/textarea'
import { sendBlob, sendJson, type Bot, type Sticker } from '@/lib/api'

const IDEA_LIMIT = 200

/** 添加表情 (pictures from this computer) and 画一张 (a model draws one, as a bot's character if one is chosen). */
export function StickerAdd({ bots, canDraw, reload }: { bots: Bot[]; canDraw: boolean; reload: () => void }) {
  const adding = useAction(reload)
  const drawing = useAction(reload)
  const [added, setAdded] = useState('')
  const [idea, setIdea] = useState('')
  const [botId, setBotId] = useState('')
  const picker = useRef<HTMLInputElement>(null)
  const id = useId()

  const upload = async (files: File[]) => {
    setAdded('')
    let fresh = 0
    let known = 0
    await adding.run(async () => {
      for (const file of files) {
        try {
          const result = await sendBlob<{ added: boolean }>('/api/stickers/upload', file)
          if (result.added) fresh += 1
          else known += 1
        } catch (e) {
          throw new Error(`${file.name}：${(e as Error).message}`)
        }
      }
    })
    if (fresh + known > 0) {
      setAdded([fresh && `加了 ${fresh} 张`, known && `${known} 张和已有的重复，没有再加（已有的标成了保留）`].filter(Boolean).join('，'))
    }
  }

  const draw = async () => {
    setAdded('')
    const done = await drawing.run(() =>
      sendJson<Sticker>('POST', '/api/stickers/draw', { idea: idea.trim(), bot_id: botId || null }),
    )
    if (done) {
      setIdea('')
      setAdded('画好了，看图模型写完描述后就能发')
    }
  }

  return (
    <section className="space-y-4 rounded-2xl border bg-card p-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-base font-semibold tracking-tight">自己加表情</h2>
        <input
          ref={picker}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          multiple
          hidden
          aria-label="选择表情图片"
          onChange={(e) => {
            const files = [...(e.target.files ?? [])]
            e.target.value = ''
            if (files.length > 0) void upload(files)
          }}
        />
        <Button size="sm" variant="outline" disabled={adding.busy} onClick={() => picker.current?.click()}>
          {adding.busy ? <LoaderCircle className="animate-spin" /> : <ImagePlus />}
          添加表情
        </Button>
        <p className="text-xs text-muted-foreground">PNG、JPEG、GIF、WebP，每张最多 10 MB。加的和画的都不会被自动清掉。</p>
      </div>

      <div className="space-y-2">
        <Label htmlFor={`${id}-idea`}>画一张</Label>
        <Textarea
          id={`${id}-idea`}
          rows={2}
          maxLength={IDEA_LIMIT}
          placeholder="想要什么表情，比如：举着写有「好耶」的小牌子，开心地跳起来"
          value={idea}
          disabled={drawing.busy || !canDraw}
          onChange={(e) => setIdea(e.target.value)}
        />
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect
            aria-label="画谁"
            value={botId}
            disabled={drawing.busy || !canDraw}
            onChange={(e) => setBotId(e.target.value)}
          >
            <option value="">不指定角色（所有 bot 都能发）</option>
            {bots.map((bot) => (
              <option key={bot.id} value={bot.id}>
                画{bot.name}（只有她能发）
              </option>
            ))}
          </NativeSelect>
          <Button size="sm" disabled={drawing.busy || !canDraw || !idea.trim()} onClick={() => void draw()}>
            {drawing.busy ? <LoaderCircle className="animate-spin" /> : <Palette />}
            {drawing.busy ? '正在画' : '画一张'}
          </Button>
          {drawing.busy && <p className="text-xs text-muted-foreground">要一分钟左右，别关这一页</p>}
        </div>
        {!canDraw && (
          <p className="text-xs text-muted-foreground">画图要用 Codex（ChatGPT 订阅）模型，先在「模型」页加一个。</p>
        )}
      </div>

      {(adding.error || drawing.error) && (
        <p role="alert" className="text-sm text-destructive">
          {adding.error || drawing.error}
        </p>
      )}
      {added && <p className="text-sm text-muted-foreground">{added}</p>}
    </section>
  )
}
