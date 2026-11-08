import { useState } from 'react'

import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { NewTextForm, TextCard } from '@/components/TextCard'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { sendJson, type Persona } from '@/lib/api'
import { usePersonas } from '@/lib/live'

const NAME_MAX = 40
const CONTENT_MAX = 4000
const EXAMPLES_MAX = 1000

/** 人设: who each bot is told it is, assigned per group on the Bot page (发言 → 人设). */
export function Personas() {
  const personas = usePersonas()
  if (!personas.data) return <FirstLoad error={personas.error} what="人设" />
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        bot 的人设：它是谁、什么性格、怎么称呼人。在「Bot」页每个群的「发言」里选用；同一个人设可以给几个群、几个 bot 用。改了马上生效。说话的腔调可以另外在「语气」页选。
      </p>
      <StaleNotice error={personas.error} />
      <NewTextForm
        title="新建人设"
        nameLabel="名字"
        bodyLabel="人设内容"
        namePlaceholder="比如：答疑小助手（交流群）"
        bodyPlaceholder="你是答疑小助手，……"
        nameMax={NAME_MAX}
        bodyMax={CONTENT_MAX}
        onCreate={(name, content) => sendJson('POST', '/api/personas', { name, content })}
        reload={personas.reload}
      />
      {personas.data.map((persona) => (
        <div key={persona.id} className="flex flex-col gap-2">
        <TextCard
          name={persona.name}
          body={persona.content}
          nameLabel="名字"
          bodyLabel="人设内容"
          nameMax={NAME_MAX}
          bodyMax={CONTENT_MAX}
          rows={8}
          usedBy={persona.used_by.map((u) => `${u.bot_name}「${u.channel_name}」`)}
          onSave={(name, content) => sendJson('PATCH', `/api/personas/${persona.id}`, { name, content })}
          onDelete={() => sendJson('DELETE', `/api/personas/${persona.id}`)}
          reload={personas.reload}
        />
        <Examples key={persona.examples} persona={persona} reload={personas.reload} />
        </div>
      ))}
    </div>
  )
}

/** 说话示例: a few lines of how this persona talks, which hold a voice better than describing it. */
function Examples({ persona, reload }: { persona: Persona; reload: () => void }) {
  const [text, setText] = useState(persona.examples)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const save = async () => {
    setSaving(true)
    setError('')
    try {
      await sendJson('PATCH', `/api/personas/${persona.id}`, { examples: text })
      reload()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }
  return (
    <details className="rounded-lg border px-3 py-2 text-sm" open={persona.examples !== ''}>
      <summary className="cursor-pointer text-muted-foreground">说话示例（{persona.name}）</summary>
      <div className="mt-2 flex flex-col gap-2">
        <p className="text-muted-foreground">写 2 到 4 组就够，模型只学语气和长短，不会照抄。</p>
        <Textarea
          aria-label={`${persona.name} 的说话示例`}
          rows={5}
          maxLength={EXAMPLES_MAX}
          value={text}
          placeholder={'有人：今天好累\n你：那就早点睡，别硬撑'}
          onChange={(e) => setText(e.target.value)}
        />
        {error && (
          <p role="alert" className="text-destructive">
            {error}
          </p>
        )}
        <div>
          <Button type="button" size="sm" disabled={saving || text === persona.examples} onClick={() => void save()}>
            保存示例
          </Button>
        </div>
      </div>
    </details>
  )
}
