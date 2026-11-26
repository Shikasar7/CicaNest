import { useId, useRef, useState, type ChangeEvent } from 'react'
import { FileUp, ImageUp, Plus, Trash2 } from 'lucide-react'

import { AvatarCropper } from '@/components/bots/AvatarCropper'
import { ModelSelect } from '@/components/chat/ModelSelect'
import { Samples } from '@/components/chat/Samples'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Textarea } from '@/components/ui/textarea'
import { avatarPath, sendBlob, sendJson, type AvatarView } from '@/lib/api'
import { useLive, useProviders } from '@/lib/live'
import { usable } from '@/lib/models'
import { readPicture } from '@/lib/pictures'
import { cn } from '@/lib/utils'
import { SkillTexts } from '@/pages/Plugins'

/** 新伙伴 / 搬一个进来 (伙伴模式). A new companion lives only on the desk: a name and a model are enough. Bringing
 * one in reads her keepsake (.md / .txt / .zip), lets a model draft her persona and what to remember of you,
 * and shows all of it to change before anything is kept. */

type Kind = 'like' | 'fact' | 'promise'
type Point = { kind: Kind; content: string; due: string; same?: boolean }
/** `same`: she has it word for word; `other`: one of hers with this title and another text. */
type Record = { title: string; body: string; source: string; keep: boolean; same?: boolean; other?: number | null; replace?: boolean }
type Todo = { text: string; due: string; keep?: boolean; as?: 'reminder' | 'task' }
type Draft = {
  name: string
  aliases: string[]
  user_name: string
  persona: string
  examples: string
  points: Point[]
  records: Record[]
  todos?: Todo[]
  cut: string[]
}
export type DeskTarget = { bot_id: string; name: string }

const KINDS: { value: Kind; label: string }[] = [
  { value: 'fact', label: '情况' },
  { value: 'like', label: '喜好' },
  { value: 'promise', label: '约定' },
]
const POINT_MAX = 60
const FILE_MAX_MB = 2

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.onerror = () => reject(new Error(`读不了 ${file.name}`))
    reader.readAsDataURL(file)
  })
}

const TABS = [
  ['samples', '示例伙伴'],
  ['new', '自己写一个'],
  ['import', '从备份搬一个进来'],
  ['pack', '导入伙伴包'],
] as const

export function NewCompanion({ targets, own = false, onDone, onCancel }: {
  targets: DeskTarget[]
  /** Opened to write one of your own. */
  own?: boolean
  onDone: (botId: string) => void
  onCancel: () => void
}) {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>(own ? 'new' : targets.length ? 'import' : 'samples')
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b px-4 py-2">
        {TABS.map(([t, label]) => (
          <button
            key={t}
            type="button"
            aria-pressed={tab === t}
            onClick={() => setTab(t)}
            className={cn(
              'rounded-lg px-3 py-1.5 text-sm',
              tab === t ? 'bg-muted font-medium' : 'text-muted-foreground hover:bg-muted/60',
            )}
          >
            {label}
          </button>
        ))}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onCancel}>
          关闭
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {tab === 'samples' ? (
          <div className="mx-auto max-w-2xl">
            <Samples onDone={onDone} />
          </div>
        ) : tab === 'new' ? (
          <Blank onDone={onDone} />
        ) : tab === 'pack' ? (
          <ImportPack onDone={onDone} />
        ) : (
          <Import targets={targets} onDone={onDone} />
        )}
      </div>
    </div>
  )
}

/** Her picture, chosen and cropped before she exists: uploaded right after she is made. */
type Picked = { blob: Blob; preview: string }

function asDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('图片读不出来'))
    reader.readAsDataURL(blob)
  })
}

/** 头像（可选）: pick, crop, and see it before she is made. */
function AvatarField({ value, onChange }: { value: Picked | null; onChange: (picked: Picked | null) => void }) {
  const [cropping, setCropping] = useState<string | null>(null)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const pick = async (file: File | undefined) => {
    if (!file) return
    setError('')
    try {
      setCropping(await readPicture(file))
    } catch (e) {
      setError((e as Error).message)
    }
  }
  return (
    <div className="space-y-1">
      <Label>头像（可以先不选，之后在伙伴页换）</Label>
      {cropping ? (
        <AvatarCropper
          src={cropping}
          busy={false}
          onCancel={() => setCropping(null)}
          onPickAnother={() => input.current?.click()}
          onConfirm={(blob) => {
            void asDataUrl(blob).then(
              (preview) => {
                onChange({ blob, preview })
                setCropping(null)
              },
              (e: Error) => setError(e.message),
            )
          }}
          onError={setError}
          themed
        />
      ) : (
        <div className="flex items-center gap-3">
          {value ? (
            <img src={value.preview} alt="她的头像" className="size-14 rounded-full object-cover ring-2 ring-border" />
          ) : (
            <span className="grid size-14 place-items-center rounded-full bg-muted text-muted-foreground">
              <ImageUp className="size-5" />
            </span>
          )}
          <Button type="button" variant="outline" size="sm" onClick={() => input.current?.click()}>
            {value ? '换一张' : '选一张图'}
          </Button>
          {value && (
            <Button type="button" variant="ghost" size="sm" onClick={() => onChange(null)}>
              不要了
            </Button>
          )}
        </div>
      )}
      <input
        ref={input}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0]
          e.target.value = ''
          void pick(file)
        }}
      />
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

/** Her picture, once she exists. A picture that does not go up leaves her made: it can be set on the Bot page. */
function useUploadAvatar() {
  const { setAvatar } = useLive()
  return async (botId: string, picked: Picked | null): Promise<string> => {
    if (!picked) return ''
    try {
      setAvatar(botId, await sendBlob<AvatarView>(`${avatarPath(botId)}/custom`, picked.blob))
      return ''
    } catch (e) {
      return `头像没传上去（${(e as Error).message}），可以在伙伴页再换`
    }
  }
}

function Blank({ onDone }: { onDone: (botId: string) => void }) {
  const { data: providers } = useProviders()
  const models = usable(providers)
  const [name, setName] = useState('')
  const [provider, setProvider] = useState('')
  const [persona, setPersona] = useState('')
  const [picked, setPicked] = useState<Picked | null>(null)
  const upload = useUploadAvatar()
  const [state, setState] = useState<'idle' | 'busy' | string>('idle')
  const ids = { name: useId(), model: useId(), persona: useId() }
  const chosen = provider || models[0]?.id || ''

  const make = async () => {
    setState('busy')
    try {
      const made = await sendJson<{ bot_id: string }>('POST', '/api/companions', {
        name: name.trim(),
        provider_id: chosen || null,
        persona: persona.trim(),
      })
      const failed = await upload(made.bot_id, picked)
      if (failed) window.alert(failed)
      setState('idle')
      onDone(made.bot_id)
    } catch (e) {
      setState((e as Error).message)
    }
  }

  return (
    <div className="max-w-xl space-y-4 text-sm">
      <p className="text-muted-foreground">
        新伙伴只在桌面上：不需要 QQ 号，在这里和桌面端跟她聊。有自己的人设、模型和记忆。以后想让她也去 QQ、Discord 这些地方，在伙伴页给她接一个平台。
      </p>
      <div className="space-y-1">
        <Label htmlFor={ids.name}>名字</Label>
        <Input id={ids.name} value={name} maxLength={20} onChange={(e) => setName(e.target.value)} />
      </div>
      <AvatarField value={picked} onChange={setPicked} />
      <div className="space-y-1">
        <Label htmlFor={ids.model}>模型</Label>
        <ModelSelect id={ids.model} value={chosen} onChange={setProvider} providers={models} optional />
      </div>
      <div className="space-y-1">
        <Label htmlFor={ids.persona}>人设（可以先空着，之后在「人设」页写）</Label>
        <Textarea
          id={ids.persona}
          value={persona}
          maxLength={4000}
          rows={6}
          placeholder="你是……"
          onChange={(e) => setPersona(e.target.value)}
        />
      </div>
      <Button disabled={!name.trim() || state === 'busy'} onClick={() => void make()}>
        <Plus className="size-4" />
        创建
      </Button>
      {state !== 'idle' && state !== 'busy' && <p role="alert" className="text-destructive">{state}</p>}
    </div>
  )
}

function Import({ targets, onDone }: { targets: DeskTarget[]; onDone: (botId: string) => void }) {
  const { data: providers } = useProviders()
  const models = usable(providers)
  const [files, setFiles] = useState<File[]>([])
  const [hint, setHint] = useState('')
  const [provider, setProvider] = useState('')
  const [target, setTarget] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [had, setHad] = useState(0) // points she has already, left out
  const [picked, setPicked] = useState<Picked | null>(null)
  const upload = useUploadAvatar()
  const [state, setState] = useState<'idle' | 'reading' | 'saving' | string>('idle')
  const ids = { files: useId(), hint: useId(), model: useId(), target: useId() }
  const chosen = provider || models[0]?.id || ''
  const busy = state === 'reading' || state === 'saving'
  const problem = state !== 'idle' && !busy ? state : ''

  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? [])
    const big = picked.find((f) => f.size > FILE_MAX_MB * 1_000_000)
    setState(big ? `${big.name} 太大了（最多 ${FILE_MAX_MB} MB）` : 'idle')
    setFiles(big ? [] : picked)
    setDraft(null)
  }

  const read = async () => {
    setState('reading')
    try {
      const uploads = await Promise.all(files.map(async (f) => ({ name: f.name, data: await toBase64(f) })))
      const found = await sendJson<Draft>('POST', '/api/companions/preview', {
        provider_id: chosen,
        name: hint.trim(),
        files: uploads,
        bot_id: target || null,
      })
      setDraft({ ...found, points: found.points.filter((p) => !p.same) })
      setHad(found.points.filter((p) => p.same).length)
      setState('idle')
    } catch (e) {
      setState(`没读成：${(e as Error).message}`)
    }
  }

  const save = async () => {
    if (!draft) return
    setState('saving')
    const records = draft.records
      .filter((r) => r.keep)
      .map(({ title, body, source, other, replace }) => ({ title, body, source, replace: replace ? other : null }))
    const points = draft.points.filter((p) => p.content.trim()).map(({ kind, content, due }) => ({ kind, content, due }))
    const todos = (draft.todos ?? [])
      .filter((t) => t.keep && t.text.trim())
      .map((t) => ({ text: t.text.trim(), due: (t.as ?? (t.due ? 'reminder' : 'task')) === 'reminder' ? t.due : '' }))
    try {
      const made = await sendJson<{ bot_id: string; problems?: string[] }>('POST', '/api/companions', {
        bot_id: target || null,
        name: draft.name.trim(),
        aliases: draft.aliases,
        provider_id: target ? null : chosen || null,
        persona: draft.persona,
        examples: draft.examples,
        user_name: draft.user_name.trim(),
        points,
        records,
        todos,
      })
      const failed = [await upload(made.bot_id, picked), ...(made.problems ?? [])].filter(Boolean)
      if (failed.length) window.alert(failed.join('\n'))
      setState('idle')
      onDone(made.bot_id)
    } catch (e) {
      setState(`没保存成：${(e as Error).message}`)
    }
  }

  const edit = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d))
  const editPoint = (n: number, patch: Partial<Point>) =>
    edit({ points: draft!.points.map((p, i) => (i === n ? { ...p, ...patch } : p)) })

  return (
    <div className="max-w-3xl space-y-5 text-sm">
      <div className="space-y-2 text-muted-foreground">
        <p>
          选她以前整理的设定和回忆（.md、.txt，或装着它们的 .zip，比如别的应用里让她写的备份）。nest 会用模型把它分成三份：
          <b className="text-foreground">人设</b>（每次都带着）、<b className="text-foreground">要记得的事</b>
          （存进长期记忆）、<b className="text-foreground">回忆档案</b>（原文保存，聊到相关的事时才翻出来）。保存前你可以改每一项。
        </p>
        <p>
          搬过来的是设定和回忆。模型不同，她说话的感觉可能和原来不完全一样；先试着聊聊，不满意再调人设和说话示例。
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1 sm:col-span-2">
          <Label htmlFor={ids.files}>文件</Label>
          <Input id={ids.files} type="file" multiple accept=".md,.markdown,.txt,.zip" onChange={pick} />
        </div>
        <div className="space-y-1">
          <Label htmlFor={ids.hint}>她叫什么（可不填）</Label>
          <Input id={ids.hint} value={hint} maxLength={20} onChange={(e) => setHint(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor={ids.model}>用哪个模型整理{target ? '' : '，也是她以后说话用的模型'}</Label>
          <ModelSelect id={ids.model} value={chosen} onChange={setProvider} providers={models} />
        </div>
        <div className="space-y-1 sm:col-span-2">
          <Label htmlFor={ids.target}>搬到</Label>
          <NativeSelect id={ids.target} value={target} onChange={(e) => setTarget(e.target.value)} className="w-full">
            <option value="">一个新伙伴（只在桌面上）</option>
            {targets.map((t) => (
              <option key={t.bot_id} value={t.bot_id}>
                已有的 {t.name}（加到她和你的私聊里）
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>
      <Button disabled={files.length === 0 || !chosen || busy} onClick={() => void read()}>
        <FileUp className="size-4" />
        {state === 'reading' ? '正在整理，可能要一两分钟…' : draft ? '重新整理' : '读取并整理'}
      </Button>
      {problem && <p role="alert" className="text-destructive">{problem}</p>}

      {draft && (
        <div className="space-y-6 border-t pt-5">
          <section className="space-y-3">
            <h3 className="font-medium">她是谁</h3>
            <AvatarField value={picked} onChange={setPicked} />
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="名字" value={draft.name} max={20} onChange={(name) => edit({ name })} disabled={!!target} />
              <Field
                label="别的叫法（用、隔开）"
                value={draft.aliases.join('、')}
                max={70}
                onChange={(v) => edit({ aliases: v.split(/[、,，]/).map((a) => a.trim()).filter(Boolean).slice(0, 3) })}
                disabled={!!target}
              />
              <Field label="她怎么叫你" value={draft.user_name} max={20} onChange={(user_name) => edit({ user_name })} />
            </div>
            <Area
              label={target ? '人设（会替换她和你私聊里现在的人设；清空就保持原样）' : '人设'}
              value={draft.persona}
              max={4000}
              rows={10}
              onChange={(persona) => edit({ persona })}
            />
            <Area
              label="说话示例（她说过的原话，一行一句）"
              value={draft.examples}
              max={1000}
              rows={4}
              onChange={(examples) => edit({ examples })}
            />
          </section>

          <section className="space-y-3">
            <h3 className="font-medium">要记得的事（{POINT_MAX} 字以内一条）</h3>
            <p className="text-muted-foreground">
              每次聊天最多带上 8 条，约定排在前面；人设里已经写了的规矩不用再列。
              {had > 0 && ` 有 ${had} 条她已经记得了，没有再列。`}
              {draft.points.length > 8 && <span className="text-warn"> 现在有 {draft.points.length} 条，多出来的有时会带不上。</span>}
            </p>
            <ul className="space-y-2">
              {draft.points.map((point, n) => (
                <li key={n} className="flex flex-wrap items-center gap-2">
                  <NativeSelect
                    aria-label="种类"
                    value={point.kind}
                    onChange={(e) => editPoint(n, { kind: e.target.value as Kind })}
                  >
                    {KINDS.map((k) => (
                      <option key={k.value} value={k.value}>
                        {k.label}
                      </option>
                    ))}
                  </NativeSelect>
                  <Input
                    aria-label="内容"
                    className="min-w-60 flex-1"
                    value={point.content}
                    maxLength={POINT_MAX}
                    onChange={(e) => editPoint(n, { content: e.target.value })}
                  />
                  {point.kind === 'promise' && (
                    <Input
                      aria-label="日子"
                      type="date"
                      className="w-40"
                      value={point.due}
                      onChange={(e) => editPoint(n, { due: e.target.value })}
                    />
                  )}
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label="删掉这条"
                    onClick={() => edit({ points: draft.points.filter((_, i) => i !== n) })}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
            {draft.points.length < 20 && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => edit({ points: [...draft.points, { kind: 'fact', content: '', due: '' }] })}
              >
                <Plus className="size-4" />
                加一条
              </Button>
            )}
          </section>

          <section className="space-y-3">
            <h3 className="font-medium">回忆档案（原文保存，勾掉的不存）</h3>
            {draft.cut.length > 0 && (
              <p className="text-warn">这些文件太长，只存前 6 万字：{draft.cut.join('、')}</p>
            )}
            <ul className="space-y-2">
              {draft.records.map((record, n) => (
                <li key={n} className="rounded-lg border p-3">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={record.keep}
                      onChange={(e) =>
                        edit({ records: draft.records.map((r, i) => (i === n ? { ...r, keep: e.target.checked } : r)) })
                      }
                    />
                    <span className="font-medium">{record.title}</span>
                    <span className="text-xs text-muted-foreground">
                      {record.source} · {record.body.length} 字
                      {record.same && ' · 她已经有一模一样的了'}
                    </span>
                  </label>
                  {record.keep && record.other != null && (
                    <label className="mt-1 flex items-center gap-2 text-xs text-warn">
                      她有一份同名的，内容不一样：
                      <NativeSelect
                        value={record.replace ? 'replace' : 'both'}
                        onChange={(e) =>
                          edit({
                            records: draft.records.map((r, i) => (i === n ? { ...r, replace: e.target.value === 'replace' } : r)),
                          })
                        }
                      >
                        <option value="both">两份都留着</option>
                        <option value="replace">用这份换掉旧的</option>
                      </NativeSelect>
                    </label>
                  )}
                  <details className="mt-1">
                    <summary className="cursor-pointer text-xs text-muted-foreground">看内容</summary>
                    <pre className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap break-words text-xs">{record.body}</pre>
                  </details>
                </li>
              ))}
            </ul>
          </section>

          {!!draft.todos?.length && (
            <section className="space-y-3">
              <h3 className="font-medium">还没做完的事（勾了才建）</h3>
              <p className="text-muted-foreground">有时间的建成提醒，没有时间的可以让她在后台去做（要她的模型会用工具）。</p>
              <ul className="space-y-2">
                {draft.todos.map((todo, n) => {
                  const as = todo.as ?? (todo.due ? 'reminder' : 'task')
                  const change = (patch: Partial<Todo>) =>
                    edit({ todos: draft.todos!.map((t, i) => (i === n ? { ...t, ...patch } : t)) })
                  return (
                    <li key={n} className="flex flex-wrap items-center gap-2">
                      <input
                        type="checkbox"
                        aria-label={`建「${todo.text}」`}
                        checked={Boolean(todo.keep)}
                        onChange={(e) => change({ keep: e.target.checked })}
                      />
                      <Input
                        aria-label="要做的事"
                        className="min-w-52 flex-1"
                        value={todo.text}
                        maxLength={POINT_MAX}
                        onChange={(e) => change({ text: e.target.value })}
                      />
                      <NativeSelect
                        aria-label="建成"
                        value={as}
                        onChange={(e) => change({ as: e.target.value as 'reminder' | 'task' })}
                      >
                        <option value="reminder">提醒我</option>
                        <option value="task">让她去做</option>
                      </NativeSelect>
                      {as === 'reminder' && (
                        <Input
                          aria-label="时间"
                          type="datetime-local"
                          className="w-52"
                          value={todo.due.replace(' ', 'T')}
                          onChange={(e) => change({ due: e.target.value.replace('T', ' ') })}
                        />
                      )}
                    </li>
                  )
                })}
              </ul>
              {draft.todos.some((t) => t.keep && (t.as ?? (t.due ? 'reminder' : 'task')) === 'reminder' && !t.due) && (
                <p className="text-xs text-warn">有勾了「提醒我」但没填时间的，会当成让她去做。</p>
              )}
            </section>
          )}

          <p className="text-muted-foreground">
            这些内容只存在运行 nest 的电脑上；聊天时会随对话发给她用的模型服务，和现在的私聊一样。
          </p>
          <Button disabled={busy || (!target && !draft.name.trim())} onClick={() => void save()}>
            {state === 'saving' ? '正在保存…' : target ? '加到她身上' : `把${draft.name || '她'}接回来`}
          </Button>
        </div>
      )}
    </div>
  )
}

function Field({ label, value, max, onChange, disabled = false }: {
  label: string
  value: string
  max: number
  onChange: (v: string) => void
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} value={value} maxLength={max} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}

function Area({ label, value, max, rows, onChange }: {
  label: string
  value: string
  max: number
  rows: number
  onChange: (v: string) => void
}) {
  const id = useId()
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>
        {label}
        <span className="ml-2 text-xs font-normal text-muted-foreground">
          {value.length}/{max}
        </span>
      </Label>
      <Textarea id={id} value={value} maxLength={max} rows={rows} onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}

type PackPreview = {
  stage: string
  plugin: { id: string; trust: string; skills?: { name: string; description: string; text?: string }[] }
  companion: {
    name: string
    persona: string
    user_name: string
    points: Point[]
    records: { title: string; chars: number }[]
  }
  extras: string[]
}
const EXTRA: { [kind: string]: string } = { skills: '技能', pets: '桌宠', mcp: 'MCP 工具', python: 'Python 模块' }

/** 导入伙伴包: a .zip exported from a nest (her persona, what she remembers, her records, maybe her skills
 * and pets), shown first, then made into a new companion on the desk. */
function ImportPack({ onDone }: { onDone: (botId: string) => void }) {
  const { data: providers } = useProviders()
  const models = usable(providers)
  const [preview, setPreview] = useState<PackPreview | null>(null)
  const [name, setName] = useState('')
  const [provider, setProvider] = useState('')
  const [state, setState] = useState<'idle' | 'busy' | string>('idle')
  const ids = { file: useId(), name: useId(), model: useId() }
  const chosen = provider || models[0]?.id || ''

  const choose = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setState('busy')
    try {
      const read = await sendBlob<PackPreview>('/api/companion-packages', file)
      setPreview(read)
      setName(read.companion.name)
      setState('idle')
    } catch (err) {
      setState((err as Error).message)
    }
  }

  const keep = async () => {
    if (!preview) return
    setState('busy')
    try {
      const made = await sendJson<{ bot_id: string }>('POST', `/api/companion-packages/${preview.stage}/import`, {
        name: name.trim(),
        provider_id: chosen || null,
      })
      onDone(made.bot_id)
    } catch (err) {
      setState((err as Error).message)
    }
  }

  const c = preview?.companion
  return (
    <div className="mx-auto max-w-2xl space-y-4 text-sm">
      <p className="text-muted-foreground">
        从 nest 导出的伙伴包（.zip）：她的人设、她记得的事、回忆档案，可能还带着她的技能和桌宠。先看一遍，再建成一个新的桌面伙伴。
      </p>
      <div className="space-y-1">
        <Label htmlFor={ids.file}>选伙伴包</Label>
        <Input id={ids.file} type="file" accept=".zip" onChange={(e) => void choose(e)} disabled={state === 'busy'} />
      </div>
      {c && preview && (
        <div className="space-y-3 rounded-lg border p-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor={ids.name}>名字</Label>
              <Input id={ids.name} value={name} maxLength={20} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor={ids.model}>用哪个模型</Label>
              <ModelSelect id={ids.model} value={chosen} onChange={setProvider} providers={models} optional />
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">人设</div>
            <p className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-xs">{c.persona || '（没有）'}</p>
          </div>
          <div className="text-xs text-muted-foreground">
            她记得的事 {c.points.length} 条{c.user_name && `（她叫你「${c.user_name}」）`} · 回忆档案 {c.records.length} 份
            {preview.extras.length > 0 && ` · 还带着：${preview.extras.map((k) => EXTRA[k] ?? k).join('、')}（只给她用）`}
          </div>
          {c.points.length > 0 && (
            <ul className="max-h-32 list-disc overflow-y-auto pl-5 text-xs">
              {c.points.map((p, i) => (
                <li key={i}>{p.content}</li>
              ))}
            </ul>
          )}
          {!!preview.plugin.skills?.length && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              包里的技能是做事的方法，她会照着做（包括跑命令、联网）。建之前把全文读一遍。
            </p>
          )}
          <SkillTexts skills={preview.plugin.skills} />
          <Button disabled={!name.trim() || state === 'busy'} onClick={() => void keep()}>
            <Plus className="size-4" />
            建成新伙伴
          </Button>
        </div>
      )}
      {state !== 'idle' && state !== 'busy' && <p role="alert" className="text-xs text-destructive">{state}</p>}
    </div>
  )
}
