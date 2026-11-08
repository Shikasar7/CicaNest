import { useEffect, useId, useRef, useState } from 'react'
import { AudioLines, Download, LoaderCircle, Play, Plus, Trash2 } from 'lucide-react'

import { useAction } from '@/components/bots/shared'
import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import {
  fetchAudio,
  sendJson,
  type Bot,
  type SpeechService,
  type SpeechServiceId,
  type SpeechSettings,
  type SpeechView,
  type SpeechVoice,
} from '@/lib/api'
import { useBots } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'

const KEY_HINT: Record<SpeechServiceId, string> = {
  fish: '在 fish.audio 的 API Keys 里创建。音色 ID 是音色页链接最后那串。',
  minimax: '在 MiniMax 开放平台的「接口密钥」里创建，密钥只在创建它的站点能用。',
  openai: 'SiliconFlow、OpenAI、中转站或自己跑的模型，只要有 /audio/speech。本地服务没有密钥就不填。',
}

/** 试听: `listen` plays a voice; `playing` is the voice being fetched, `error` why it could not. */
function useListen() {
  const [playing, setPlaying] = useState('')
  const [error, setError] = useState('')
  const audio = useRef<HTMLAudioElement | null>(null)
  useEffect(() => () => audio.current?.pause(), [])
  const listen = async (voice: string) => {
    setPlaying(voice)
    setError('')
    try {
      const url = URL.createObjectURL(await fetchAudio('/api/speech/preview', { voice }))
      audio.current?.pause()
      audio.current = new Audio(url)
      audio.current.onended = () => URL.revokeObjectURL(url)
      await audio.current.play()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPlaying('')
    }
  }
  return { playing, error, listen }
}

/** A key box: shows only whether one is saved; what is typed is saved when it loses focus. */
function KeyField({ service, save }: { service: SpeechService; save: (key: string) => Promise<boolean> }) {
  const [value, setValue] = useState('')
  const id = useId()
  const commit = async () => {
    if (value.trim() && (await save(value.trim()))) setValue('')
  }
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>API Key</Label>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          type="password"
          autoComplete="off"
          className="w-72"
          placeholder={service.key_set ? '已保存，填新的会替换' : '粘贴密钥'}
          disabled={service.key_from_env}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onBlur={() => void commit()}
        />
        {service.key_set && !service.key_from_env && (
          <Button size="sm" variant="ghost" onClick={() => void save('')}>
            清除
          </Button>
        )}
      </div>
      {service.key_from_env && <p className="text-xs text-muted-foreground">这个密钥写在环境变量里，这里改不了</p>}
    </div>
  )
}

/** A text setting saved when it loses focus. */
function TextField({
  label,
  value,
  placeholder,
  save,
}: {
  label: string
  value: string
  placeholder?: string
  save: (next: string) => void
}) {
  const [draft, setDraft] = useState(value)
  const id = useId()
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        className="w-72"
        placeholder={placeholder}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          if (draft.trim() !== value) save(draft.trim())
        }}
      />
    </div>
  )
}

/** A service's 音色库: voice ids written down with a name, to pick from for the bots. */
function VoiceLibrary({
  service,
  voices,
  patch,
}: {
  service: SpeechService
  voices: SpeechVoice[]
  patch: (body: Record<string, unknown>) => Promise<boolean>
}) {
  const [voiceId, setVoiceId] = useState('')
  const [name, setName] = useState('')
  const reading = useAction(() => {})
  const { playing, error, listen } = useListen()
  const id = useId()
  const save = (next: SpeechVoice[]) => patch({ library: { [service.id]: next } })
  const add = async () => {
    if (voiceId.trim() && (await save([...voices, { id: voiceId.trim(), name: name.trim() }]))) {
      setVoiceId('')
      setName('')
    }
  }
  const readAccount = () =>
    void reading.run(async () => {
      const found = await sendJson<{ voices: SpeechVoice[] }>('POST', '/api/speech/voices', { service: service.id })
      await save([...voices, ...found.voices.filter((v) => !voices.some((known) => known.id === v.id))])
    })

  return (
    <div className="space-y-2 border-t pt-3">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-medium">音色库</h3>
        {service.id !== 'openai' && (
          <Button size="sm" variant="outline" disabled={reading.busy || !service.key_set} onClick={readAccount}>
            {reading.busy ? <LoaderCircle className="animate-spin" /> : <Download />}
            读取账户里的音色
          </Button>
        )}
      </div>
      {voices.length > 0 && (
        <ul className="divide-y">
          {voices.map((voice) => {
            const ref = `${service.id}:${voice.id}`
            return (
              <li key={voice.id} className="flex flex-wrap items-center gap-2 py-1.5">
                <span className="min-w-24 text-sm">{voice.name || '未命名'}</span>
                <code className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{voice.id}</code>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`试听${voice.name || voice.id}`}
                  disabled={Boolean(playing) || !service.ready}
                  onClick={() => void listen(ref)}
                >
                  {playing === ref ? <LoaderCircle className="animate-spin" /> : <Play />}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`删掉${voice.name || voice.id}`}
                  onClick={() => void save(voices.filter((v) => v.id !== voice.id))}
                >
                  <Trash2 />
                </Button>
              </li>
            )
          })}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="w-36"
          aria-label="音色名字"
          placeholder="名字，比如 小马"
          maxLength={40}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Input
          id={`${id}-id`}
          className="w-72"
          aria-label="音色 ID"
          placeholder={service.id === 'fish' ? '音色 ID，或音色页链接' : '音色 ID'}
          maxLength={300}
          value={voiceId}
          onChange={(e) => setVoiceId(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void add()
          }}
        />
        <Button size="sm" variant="outline" disabled={!voiceId.trim()} onClick={() => void add()}>
          <Plus />
          记下来
        </Button>
      </div>
      {(error || reading.error) && (
        <p role="alert" className="text-xs text-destructive">
          {error || reading.error}
        </p>
      )}
    </div>
  )
}

function ServiceCard({
  service,
  view,
  patch,
}: {
  service: SpeechService
  view: SpeechView
  patch: (body: Record<string, unknown>) => Promise<boolean>
}) {
  const settings = view.settings
  const id = useId()
  return (
    <section className="space-y-3 rounded-2xl border bg-card p-5">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold tracking-tight">{service.label}</h2>
        <span className={service.ready ? 'text-xs text-emerald-700 dark:text-emerald-400' : 'text-xs text-muted-foreground'}>
          {service.ready ? '可以用' : '还没设好'}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">{KEY_HINT[service.id]}</p>
      <KeyField service={service} save={(key) => patch({ [`${service.id}_key`]: key })} />
      {service.id === 'fish' && (
        <div className="space-y-1">
          <Label htmlFor={`${id}-model`}>模型</Label>
          <NativeSelect
            id={`${id}-model`}
            value={settings.fish_model}
            onChange={(e) => void patch({ fish_model: e.target.value })}
          >
            {view.fish_models.map((model) => (
              <option key={model} value={model}>
                {model}
                {model.endsWith('-free') ? '（免费）' : ''}
              </option>
            ))}
          </NativeSelect>
        </div>
      )}
      {service.id === 'minimax' && (
        <div className="flex flex-wrap gap-4">
          <div className="space-y-1">
            <Label htmlFor={`${id}-region`}>站点</Label>
            <NativeSelect
              id={`${id}-region`}
              value={settings.minimax_region}
              onChange={(e) => void patch({ minimax_region: e.target.value })}
            >
              <option value="cn">国内站</option>
              <option value="intl">国际站</option>
            </NativeSelect>
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${id}-model`}>模型</Label>
            <NativeSelect
              id={`${id}-model`}
              value={settings.minimax_model}
              onChange={(e) => void patch({ minimax_model: e.target.value })}
            >
              {view.minimax_models.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </NativeSelect>
          </div>
        </div>
      )}
      {service.id === 'openai' && (
        <div className="flex flex-wrap gap-4">
          <TextField
            label="接口地址"
            value={settings.openai_url}
            placeholder="https://api.siliconflow.cn/v1"
            save={(next) => void patch({ openai_url: next })}
          />
          <TextField
            label="模型"
            value={settings.openai_model}
            placeholder="FunAudioLLM/CosyVoice2-0.5B"
            save={(next) => void patch({ openai_model: next })}
          />
        </div>
      )}
      <VoiceLibrary service={service} voices={settings.library[service.id] ?? []} patch={patch} />
    </section>
  )
}

const voiceParts = (voice: string): [SpeechServiceId | '', string] => {
  const cut = voice.indexOf(':')
  return cut > 0 ? [voice.slice(0, cut) as SpeechServiceId, voice.slice(cut + 1)] : ['', '']
}

const CUSTOM = '__custom'

/** One bot's voice: a service, then a voice from its 音色库 or system voices, or an id typed in; and 试听. */
function BotVoice({
  bot,
  services,
  library,
  reload,
}: {
  bot: Bot
  services: SpeechService[]
  library: SpeechSettings['library']
  reload: () => void
}) {
  const [savedService, savedId] = voiceParts(bot.voice ?? '')
  const [service, setService] = useState<SpeechServiceId | ''>(savedService)
  const [voiceId, setVoiceId] = useState(savedId)
  const [typing, setTyping] = useState(false)
  const { busy, error, run } = useAction(reload)
  const listening = useListen()
  const known = services.find((s) => s.id === service)?.known ?? []
  const own = service ? (library[service] ?? []) : []
  const choices = [...own, ...known.filter((v) => !own.some((voice) => voice.id === v.id))]
  const listed = choices.some((voice) => voice.id === voiceId)
  const ref = service && voiceId.trim() ? `${service}:${voiceId.trim()}` : ''

  const save = (next: string) => {
    if (next !== (bot.voice ?? '')) void run(() => sendJson('PATCH', `/api/bots/${encodeURIComponent(bot.id)}`, { voice: next }))
  }
  const pick = (next: string) => {
    setTyping(next === CUSTOM)
    if (next === CUSTOM) return
    setVoiceId(next)
    save(service && next ? `${service}:${next}` : '')
  }

  return (
    <li className="space-y-1 border-t py-3 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-24 truncate text-sm font-medium">{bot.name}</span>
        <NativeSelect
          aria-label={`${bot.name}的语音服务`}
          value={service}
          disabled={busy}
          onChange={(e) => {
            const next = e.target.value as SpeechServiceId | ''
            setService(next)
            setVoiceId('')
            setTyping(false)
            if (!next) save('')
          }}
        >
          <option value="">不发语音</option>
          {services.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </NativeSelect>
        {service && (
          <>
            <NativeSelect
              aria-label={`${bot.name}的音色`}
              className="max-w-72"
              value={typing ? CUSTOM : voiceId}
              disabled={busy}
              onChange={(e) => pick(e.target.value)}
            >
              <option value="">选音色</option>
              {!listed && voiceId && !typing && <option value={voiceId}>{voiceId}</option>}
              {choices.map((voice) => (
                <option key={voice.id} value={voice.id}>
                  {voice.name || voice.id}
                </option>
              ))}
              <option value={CUSTOM}>手动填 ID</option>
            </NativeSelect>
            {typing && (
              <Input
                className="w-72"
                aria-label={`${bot.name}的音色 ID`}
                placeholder="音色 ID"
                value={voiceId}
                onChange={(e) => setVoiceId(e.target.value)}
                onBlur={() => save(ref)}
              />
            )}
            <Button
              size="sm"
              variant="outline"
              disabled={!ref || Boolean(listening.playing)}
              onClick={() => void listening.listen(ref)}
            >
              {listening.playing ? <LoaderCircle className="animate-spin" /> : <Play />}
              试听
            </Button>
          </>
        )}
      </div>
      {(error || listening.error) && (
        <p role="alert" className="text-xs text-destructive">
          {error || listening.error}
        </p>
      )}
    </li>
  )
}

const LIMITS: { key: keyof SpeechSettings; label: string; unit: string; min: number; max: number }[] = [
  { key: 'hourly_per_bot', label: '每个 bot 每个群每小时最多', unit: '条', min: 1, max: 60 },
  { key: 'group_gap', label: '同一个群两条语音至少隔', unit: '秒', min: 10, max: 3600 },
  { key: 'max_chars', label: '超过这么长就改发文字', unit: '字', min: 10, max: 300 },
]

/** 欢迎新人: per group, whether a bot there welcomes whoever joins. */
function WelcomeGroups({ groups, reload }: { groups: SpeechView['groups']; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  if (!groups) return null
  const toggle = (key: string, next: boolean) =>
    void run(() => sendJson('PATCH', `/api/groups/${encodeURIComponent(key)}/features`, { welcome: next }))
  return (
    <section className="space-y-3 rounded-2xl border bg-card p-5">
      <h2 className="text-base font-semibold tracking-tight">新人进群时欢迎</h2>
      <p className="text-xs text-muted-foreground">
        有人进群时，从这个群里在说话的 bot 中随机挑一个，按它的人设说一句欢迎，能发语音就用语音，发不了就发文字。同一个人十分钟内只欢迎一次，一下子进很多人时只欢迎前几个。
      </p>
      {groups.length === 0 ? (
        <p className="text-sm text-muted-foreground">还没有 QQ 群</p>
      ) : (
        <ul className="flex flex-wrap gap-x-6 gap-y-2">
          {groups.map((group) => (
            <li key={group.key}>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={group.welcome} disabled={busy} onCheckedChange={(next) => toggle(group.key, next)} />
                {group.name || group.key.split(':').pop()}
              </label>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}

/** 语音: the speech services (the user's own keys), each bot's voice, and the limits. */
export function Speech() {
  const list = useLiveData<SpeechView>('/api/speech', [])
  const bots = useBots<Bot[]>('/api/bots')
  const { error, run } = useAction(list.reload)

  if (list.missing && !list.data) {
    return (
      <p className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
        正在运行的 nest 版本还没有语音功能。重启 nest 后再打开这一页。
      </p>
    )
  }
  if (!list.data) return <FirstLoad error={list.error} what="语音设置" />
  const view = list.data
  const patch = (body: Record<string, unknown>) => run(() => sendJson('PATCH', '/api/speech', body))

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        接上自己的语音服务，把音色 ID 记进音色库，给 bot 选好音色后，在 Bot 页群的设置里打开「发语音」，bot 觉得读出来更有味道时就发一条语音。合成不出来时改发文字。
      </p>
      <StaleNotice error={list.error} />
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {view.services.map((service) => (
        <ServiceCard key={service.id} service={service} view={view} patch={patch} />
      ))}

      <WelcomeGroups groups={view.groups} reload={list.reload} />

      <section className="space-y-3 rounded-2xl border bg-card p-5">
        <h2 className="text-base font-semibold tracking-tight">给 bot 选音色</h2>
        {(bots.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">还没有 bot</p>
        ) : (
          <ul>
            {(bots.data ?? []).map((bot) => (
              <BotVoice
                key={bot.id}
                bot={bot}
                services={view.services}
                library={view.settings.library}
                reload={bots.reload}
              />
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-3 rounded-2xl border bg-card p-5">
        <h2 className="flex items-center gap-2 text-base font-semibold tracking-tight">
          <AudioLines className="size-4" />
          限制
        </h2>
        <div className="flex flex-wrap gap-x-6 gap-y-3">
          {LIMITS.map((limit) => (
            <label key={limit.key} className="flex items-center gap-2 text-sm">
              {limit.label}
              <Input
                key={String(view.settings[limit.key])}
                className="w-20"
                type="number"
                min={limit.min}
                max={limit.max}
                defaultValue={Number(view.settings[limit.key])}
                onBlur={(e) => {
                  const next = Number(e.target.value)
                  if (Number.isInteger(next) && next !== Number(view.settings[limit.key])) {
                    void patch({ [limit.key]: next })
                  }
                }}
              />
              {limit.unit}
            </label>
          ))}
        </div>
        <TextField
          label="代理（连不上国外服务时填，比如 http://127.0.0.1:7890）"
          value={view.settings.proxy}
          save={(next) => void patch({ proxy: next })}
        />
      </section>
    </div>
  )
}
