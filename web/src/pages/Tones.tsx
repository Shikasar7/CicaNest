import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { NewTextForm, TextCard } from '@/components/TextCard'
import { sendJson, type Tone } from '@/lib/api'
import { useTones } from '@/lib/live'

const LABEL_MAX = 20
const MANNER_MAX = 800

/** 语气: how bots talk. Each group picks one for 答题 and one for chat on the Bot page. */
export function Tones() {
  const tones = useTones()
  if (!tones.data) return <FirstLoad error={tones.error} what="语气" />
  const uses = (tone: Tone) =>
    tone.used_by.map((u) => `${u.bot}「${u.channel}」${u.as === 'answer' ? '答题' : '聊天'}`)
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        bot 说话的语气。在「Bot」页每个群里可以分别选「聊天语气」和「答题语气」。人设决定 bot 是谁，语气决定它怎么说话；选「跟人设走」就照人设说。
        答题时不管选哪种，都会去掉小标题、标签和客套话，免得像在写文档。改了马上生效。
      </p>
      <StaleNotice error={tones.error} />
      <NewTextForm
        title="新建语气"
        nameLabel="名字"
        bodyLabel="怎么说话（写清楚态度和口头禅，给几句例子最有效）"
        namePlaceholder="比如：中二少年"
        bodyPlaceholder="说话要明显是中二少年，第一句就听得出来：比如「哼，这点小事交给本大爷」……"
        nameMax={LABEL_MAX}
        bodyMax={MANNER_MAX}
        onCreate={(label, manner) => sendJson('POST', '/api/tones', { label, manner })}
        reload={tones.reload}
      />
      {tones.data.map((tone) => (
        <TextCard
          key={tone.key}
          name={tone.label}
          body={tone.manner}
          nameLabel="名字"
          bodyLabel="怎么说话"
          nameMax={LABEL_MAX}
          bodyMax={MANNER_MAX}
          rows={4}
          badge={
            <span className="pb-2 text-xs text-muted-foreground">{tone.builtin ? (tone.edited ? '内置（改过）' : '内置') : '自己加的'}</span>
          }
          usedBy={uses(tone)}
          onSave={(label, manner) => sendJson('PATCH', `/api/tones/${tone.key}`, { label, manner })}
          onReset={tone.builtin && tone.edited ? () => sendJson('POST', `/api/tones/${tone.key}/reset`) : undefined}
          onDelete={tone.builtin ? undefined : () => sendJson('DELETE', `/api/tones/${tone.key}`)}
          reload={tones.reload}
        />
      ))}
    </div>
  )
}
