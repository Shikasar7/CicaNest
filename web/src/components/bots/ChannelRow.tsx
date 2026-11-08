import { useId, useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'

import { PersonaControls } from '@/components/bots/PersonaControls'
import {
  ACTIVITY,
  channelPath,
  groupName,
  isPrivate,
  useAction,
  useJustSaved,
  useRememberedOpen,
} from '@/components/bots/shared'
import { Segmented } from '@/components/Segmented'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import {
  sendJson,
  type Activity,
  type Bot,
  type BotChannel,
  type ChannelPatch,
  type Ok,
  type Persona,
  type StickerMode,
  type Tone,
} from '@/lib/api'
import { CHANGED_ELSEWHERE, useDraft } from '@/lib/drafts'
import { describedBy, editKeys, parseNumber } from '@/lib/forms'
import { can } from '@/lib/platforms'
import { cn } from '@/lib/utils'

const COOLDOWN_MAX = 600

const ACTIVITY_HINT: Record<Activity, string> = {
  mentions: '只有被点名（@、叫名字、回复它）才说话',
  occasional: '被点名必回，平时偶尔主动插一句',
  active: '群里有新消息就考虑要不要说话',
}

/** 活跃度 (groups only): how readily the bot speaks unprompted. */
function ActivityControl({ bot, channel, reload }: { bot: Bot; channel: BotChannel; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const activity = useJustSaved(channel.activity)
  const id = useId()
  const setActivity = async (next: Activity) => {
    activity.mark(next)
    const body: ChannelPatch = { activity: next }
    if (!(await run(() => sendJson<Ok>('PATCH', channelPath(bot.id, channel.channel_id), body)))) activity.drop()
  }
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={id} className="text-xs font-normal text-muted-foreground">
          活跃度
        </Label>
        <NativeSelect
          id={id}
          value={activity.value}
          disabled={busy || !channel.enabled}
          onChange={(e) => void setActivity(e.target.value as Activity)}
          aria-describedby={`${id}-hint`}
        >
          {ACTIVITY.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </NativeSelect>
        <span id={`${id}-hint`} className="text-xs text-muted-foreground">
          {channel.enabled ? ACTIVITY_HINT[activity.value] : '已关闭，打开后才能改'}
        </span>
      </div>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/** The row's settings at a glance, from what is saved: shown while it is folded. */
function Summary({ channel }: { channel: BotChannel }) {
  const chips: string[] = []
  if (!channel.enabled) chips.push('已关闭')
  else {
    chips.push(isPrivate(channel) ? '每条都回' : (ACTIVITY.find((a) => a.value === channel.activity)?.label ?? channel.activity))
    if (channel.cooldown_seconds > 0) chips.push(`冷却 ${channel.cooldown_seconds} 秒`)
    if (!isPrivate(channel) && channel.reply_lines !== undefined && channel.reply_lines !== 3)
      chips.push(`最多 ${channel.reply_lines} 条`)
    if (!isPrivate(channel) && (channel.reply_lines ?? 3) > 1 && (channel.split_rate ?? 100) < 100)
      chips.push(`${channel.split_rate}% 分条`)
    if (!isPrivate(channel) && channel.sticker_mode === 'on') chips.push('发表情')
    if (!isPrivate(channel) && channel.sticker_mode === 'trial') chips.push('表情试运行')
    if (channel.voice_mode === 'on') chips.push('发语音')
    if (channel.voice_mode === 'trial') chips.push('语音试运行')
    if (!isPrivate(channel) && channel.use_slang) chips.push('黑话')
    if (channel.answer_questions) chips.push('答题')
  }
  if (!isPrivate(channel) && channel.recorder) chips.push('记录官')
  return (
    <span className="flex min-w-0 flex-wrap gap-1">
      {chips.map((chip) => (
        <span key={chip} className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] leading-4 text-muted-foreground">
          {chip}
        </span>
      ))}
    </span>
  )
}

/** A labelled part of an open row: 发言 (how often, how fast, as whom) or 回复方式 (what a reply may carry). */
function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-3">
      <h4 className="text-xs font-medium text-foreground/80">{title}</h4>
      <div className="space-y-3 border-l-2 border-border pl-3">{children}</div>
    </div>
  )
}


/** 冷却: saved on blur or Enter; Esc puts the saved value back. */
/** 群名: a platform that lists no groups tells no names either, so the user gives one. */
function NameControl({ channel, reload }: { channel: BotChannel; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const [name, setName] = useState(channel.name)
  const id = useId()
  const save = () => run(() => sendJson<Ok>('PATCH', `/api/channels/${channel.channel_id}`, { name: name.trim() }))
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={id} className="text-xs font-normal text-muted-foreground">
          群名
        </Label>
        <Input
          id={id}
          className="h-8 w-48"
          value={name}
          maxLength={40}
          placeholder="给这个群起个名字"
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void save()}
        />
        <Button size="sm" variant="outline" disabled={busy || !name.trim() || name.trim() === channel.name} onClick={() => void save()}>
          保存
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

function CooldownControl({ bot, channel, reload }: { bot: Bot; channel: BotChannel; reload: () => void }) {
  const { busy, error, setError, run } = useAction(reload)
  const saved = useJustSaved(channel.cooldown_seconds)
  const draft = useDraft(String(saved.value), undefined, busy) // blur saves at once: leaving then loses nothing
  const id = useId()
  const parsed = parseNumber(draft.value, { min: 0, max: COOLDOWN_MAX, integer: true })
  const shown = parsed.ok ? parsed.value : null

  const commit = async () => {
    if (!draft.dirty) {
      draft.clear()
      return
    }
    if (!parsed.ok || parsed.value === null) {
      setError(`冷却填 0–${COOLDOWN_MAX} 之间的整数秒`)
      return
    }
    const value = parsed.value
    if (value === saved.value) {
      draft.clear() // e.g. "030" for 30
      setError('')
      return
    }
    if (await run(() => sendJson<Ok>('PATCH', channelPath(bot.id, channel.channel_id), { cooldown_seconds: value }))) {
      saved.mark(value)
      draft.clear()
    }
  }
  const revert = () => {
    draft.clear()
    setError('')
  }

  const hintId = `${id}-hint`
  const warnId = `${id}-warn`
  const errorId = `${id}-error`
  const long = shown !== null && shown > 60
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={id} className="text-xs font-normal text-muted-foreground">
          冷却
        </Label>
        <Input
          id={id}
          className="w-16"
          inputMode="numeric"
          autoComplete="off"
          value={draft.value}
          readOnly={busy}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(error && errorId, long && warnId, hintId)}
          onChange={(e) => {
            setError('')
            draft.set(e.target.value)
          }}
          onBlur={() => void commit()}
          onKeyDown={editKeys({ onEnter: () => void commit(), onEscape: revert })}
        />
        <span className="text-xs text-muted-foreground">秒</span>
        <span id={hintId} className="text-xs text-muted-foreground">
          {busy ? '正在保存…' : '0 表示不冷却'}
        </span>
      </div>
      {error && (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {long && (
        <p id={warnId} className="text-xs text-amber-700 dark:text-amber-400">
          超过 60 秒时，冷却中被点名也不会回
        </p>
      )}
      {draft.changedElsewhere && (
        <p role="status" className="text-xs text-amber-700 dark:text-amber-400">
          {CHANGED_ELSEWHERE}
        </p>
      )}
    </div>
  )
}

const STICKER_MODES: { value: StickerMode; label: string }[] = [
  { value: 'off', label: '关闭' },
  { value: 'trial', label: '试运行' },
  { value: 'on', label: '开启' },
]

const VOICE_RATES = [
  { value: 0, label: '模型自己挑' },
  { value: 20, label: '约两成回复' },
  { value: 40, label: '约四成回复' },
  { value: 70, label: '约七成回复' },
  { value: 100, label: '每条回复' },
]

const REPLY_LINES = [1, 2, 3, 4, 5]

const SPLIT_RATES = [
  { value: 10, label: '约一成回复分条' },
  { value: 30, label: '约三成回复分条' },
  { value: 50, label: '约一半回复分条' },
  { value: 80, label: '约八成回复分条' },
  { value: 100, label: '模型想分就分' },
]

/** 一次最多发 and 分条频率: how many messages in a row one reply may come as, and how often. */
function SplitControls({ bot, channel, reload }: { bot: Bot; channel: BotChannel; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const lines = useJustSaved(channel.reply_lines ?? 3)
  const rate = useJustSaved(channel.split_rate ?? 100)
  const id = useId()
  const patch = (body: ChannelPatch) => run(() => sendJson<Ok>('PATCH', channelPath(bot.id, channel.channel_id), body))

  const setLines = async (next: number) => {
    lines.mark(next)
    if (!(await patch({ reply_lines: next }))) lines.drop()
  }
  const setRate = async (next: number) => {
    rate.mark(next)
    if (!(await patch({ split_rate: next }))) rate.drop()
  }

  // A nest from before these settings; and a private chat, whose replies go out in one piece.
  if (channel.reply_lines === undefined || isPrivate(channel)) return null
  const hint =
    lines.value === 1
      ? '每次回复只发一条；有文字时不再带表情'
      : rate.value === 100
        ? `一次最多发 ${lines.value} 条，表情也算一条`
        : `一次最多发 ${lines.value} 条，表情也算一条；其余回复只说一句`
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span id={`${id}-lines`} className="text-xs text-muted-foreground">
          一次最多发
        </span>
        <NativeSelect
          aria-labelledby={`${id}-lines`}
          aria-describedby={`${id}-hint`}
          value={lines.value}
          disabled={busy}
          onChange={(e) => void setLines(Number(e.target.value))}
        >
          {REPLY_LINES.map((count) => (
            <option key={count} value={count}>
              {count} 条
            </option>
          ))}
        </NativeSelect>
        {lines.value > 1 && (
          <NativeSelect
            aria-label="分条频率"
            aria-describedby={`${id}-hint`}
            value={rate.value}
            disabled={busy}
            onChange={(e) => void setRate(Number(e.target.value))}
          >
            {SPLIT_RATES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </NativeSelect>
        )}
      </div>
      <p id={`${id}-hint`} className="text-xs text-muted-foreground">
        {hint}
      </p>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/** 发表情 (off / trial / on), 发语音 and 用黑话: optional extras of the bot's replies; a private chat only has 发语音. */
function ExtrasControls({ bot, channel, reload }: { bot: Bot; channel: BotChannel; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const stickers = useJustSaved<StickerMode>(channel.sticker_mode ?? 'off')
  const voice = useJustSaved<StickerMode>(channel.voice_mode ?? 'off')
  const voiceRate = useJustSaved(channel.voice_rate ?? 0)
  const slang = useJustSaved(channel.use_slang ?? false)
  const id = useId()
  const group = groupName(channel)
  const patch = (body: ChannelPatch) => run(() => sendJson<Ok>('PATCH', channelPath(bot.id, channel.channel_id), body))

  const setStickers = async (next: StickerMode) => {
    if (next === 'on' && bot.mode === 'live' && !window.confirm(`${bot.name} 正在正式发言，开启后会真的在 ${group} 发表情包。继续？`))
      return
    stickers.mark(next)
    if (!(await patch({ sticker_mode: next }))) stickers.drop()
  }
  const setVoice = async (next: StickerMode) => {
    if (next === 'on' && bot.mode === 'live' && !window.confirm(`${bot.name} 正在正式发言，开启后会真的在 ${group} 发语音。继续？`))
      return
    voice.mark(next)
    if (!(await patch({ voice_mode: next }))) voice.drop()
  }
  const setVoiceRate = async (next: number) => {
    voiceRate.mark(next)
    if (!(await patch({ voice_rate: next }))) voiceRate.drop()
  }
  const setSlang = async (next: boolean) => {
    slang.mark(next)
    if (!(await patch({ use_slang: next }))) slang.drop()
  }

  // A nest from before 表情包 and 黑话; and a private chat on one from before its 发语音.
  const inPrivate = isPrivate(channel)
  if (channel.sticker_mode === undefined || (inPrivate && channel.voice_mode === undefined)) return null
  const pictures = can(bot, 'send_image')
  const speaks = can(bot, 'send_voice')
  const hears = can(bot, 'sees_all')
  if (!pictures && !speaks && !hears) return null // 企业微信: words only
  const hint = {
    off: '不发表情',
    trial: '回复时可以选一张表情，但只记在判断里，不真的发',
    on: bot.mode === 'live' ? '回复时可以带一张表情发出去' : 'bot 正式发言后才会真的发',
  }[stickers.value]
  const voiceHint = {
    off: '',
    trial: '；语音只记在判断里，照常发文字',
    on: bot.voice ? '；可以发语音' : '；要先在「语音」页给它选音色才会发语音',
  }[voice.value]
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {!inPrivate && pictures && (
          <div className="flex items-center gap-2">
            <span id={`${id}-stickers`} className="text-xs text-muted-foreground">
              发表情
            </span>
            <Segmented
              labelledBy={`${id}-stickers`}
              describedBy={`${id}-hint`}
              disabled={busy}
              options={STICKER_MODES}
              value={stickers.value}
              onChange={(next) => void setStickers(next)}
            />
          </div>
        )}
        {channel.voice_mode !== undefined && speaks && (
          <div className="flex items-center gap-2">
            <span id={`${id}-voice`} className="text-xs text-muted-foreground">
              发语音
            </span>
            <Segmented
              labelledBy={`${id}-voice`}
              describedBy={`${id}-hint`}
              disabled={busy}
              options={STICKER_MODES}
              value={voice.value}
              onChange={(next) => void setVoice(next)}
            />
            {voice.value !== 'off' && channel.voice_rate !== undefined && (
              <NativeSelect
                aria-label="语音频率"
                value={voiceRate.value}
                disabled={busy}
                onChange={(e) => void setVoiceRate(Number(e.target.value))}
              >
                {VOICE_RATES.map((rate) => (
                  <option key={rate.value} value={rate.value}>
                    {rate.label}
                  </option>
                ))}
              </NativeSelect>
            )}
          </div>
        )}
        {!inPrivate && hears && (
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <Switch checked={slang.value} disabled={busy} onCheckedChange={(next) => void setSlang(next)} />
            用黑话
          </label>
        )}
      </div>
      <p id={`${id}-hint`} className="text-xs text-muted-foreground">
        {inPrivate ? voiceHint.slice(1) || '私聊只发文字' : hint + voiceHint}
        {voice.value !== 'off' && voiceRate.value > 0 && '，频率还受「语音」页的上限管着'}
        {!inPrivate && slang.value && '；回复时会看这个群学到的黑话'}
      </p>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/** A 语气 picker (the 「语气」 page's list): for 答题 (`which` "answer") or everyday chat ("chat"). */
function ToneSelect({
  bot,
  channel,
  tones,
  which,
  reload,
}: {
  bot: Bot
  channel: BotChannel
  tones: Tone[] | null
  which: 'answer' | 'chat'
  reload: () => void
}) {
  const { busy, error, run } = useAction(reload)
  const id = useId()
  const value = which === 'answer' ? channel.answer_tone : channel.chat_tone
  if (value === undefined || !tones) return null // a nest from before 语气, or the list is not here yet
  const current = tones.find((t) => t.key === value)
  const save = (key: string) =>
    void run(() =>
      sendJson<Ok>('PATCH', channelPath(bot.id, channel.channel_id), (which === 'answer' ? { answer_tone: key } : { chat_tone: key }) satisfies ChannelPatch),
    )
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={`${id}-tone`} className="text-xs font-normal text-muted-foreground">
          {which === 'answer' ? '答题语气' : '聊天语气'}
        </Label>
        <NativeSelect id={`${id}-tone`} value={current ? value : ''} disabled={busy} onChange={(e) => save(e.target.value)}>
          {!current && <option value="">（这个语气已删除，暂用默认）</option>}
          {tones.map((t) => (
            <option key={t.key} value={t.key}>
              {t.label}
            </option>
          ))}
        </NativeSelect>
        <a href="#/tones" className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
          编辑语气
        </a>
      </div>
      <p className="line-clamp-2 text-xs text-muted-foreground">
        {which === 'answer' ? '认真回答问题时怎么说话，内容一样严谨。' : '平时聊天怎么说话。'}
        {current && current.key !== 'persona' ? current.manner : '照人设说话，不额外加腔调。'}
      </p>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/** 答题: someone who calls the bot with a real question gets a proper answer. Turned on and off by hand;
 * only a Codex (ChatGPT subscription) model can search the web for it. */
function AnswerControl({
  bot,
  channel,
  tones,
  reload,
}: {
  bot: Bot
  channel: BotChannel
  tones: Tone[] | null
  reload: () => void
}) {
  const { busy, error, run } = useAction(reload)
  const answering = useJustSaved(channel.answer_questions ?? false)
  const id = useId()

  const toggle = async (next: boolean) => {
    answering.mark(next)
    const body: ChannelPatch = { answer_questions: next }
    if (!(await run(() => sendJson<Ok>('PATCH', channelPath(bot.id, channel.channel_id), body)))) answering.drop()
  }

  if (channel.answer_questions === undefined) return null // a nest from before 答题
  const searches = bot.provider?.kind === 'codex'
  const who = isPrivate(channel) ? '对方说话' : '有人点名'
  const hint = !answering.value
    ? '关闭：照平时聊天'
    : searches
      ? `${who}时先分辨是闲聊还是正经问题：问题就先联网查再认真答，整段发一条，不带参考链接；问以前群里的事（比如「上次谁发的链接」）就翻聊天记录答；闲聊照旧。每小时最多联网查 20 次`
      : `${who}时先分辨是闲聊还是正经问题：问题就认真答，整段发一条，但这个模型不能联网，只凭记得的答（联网要用 Codex 订阅的模型）；问以前群里的事就翻聊天记录答`
  return (
    <div className="space-y-1">
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <Switch
          checked={answering.value}
          disabled={busy}
          onCheckedChange={(next) => void toggle(next)}
          aria-describedby={`${id}-hint`}
        />
        答题
      </label>
      <p id={`${id}-hint`} className="text-xs text-muted-foreground">
        {hint}
      </p>
      {answering.value && <ToneSelect bot={bot} channel={channel} tones={tones} which="answer" reload={reload} />}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/** 问题记录官: this bot writes down what the bots could not solve in the group (one bot per group). */
function RecorderControl({ bot, channel, reload }: { bot: Bot; channel: BotChannel; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const recording = useJustSaved(channel.recorder ?? false)
  const id = useId()

  const toggle = async (next: boolean) => {
    recording.mark(next)
    const body: ChannelPatch = { recorder: next }
    if (!(await run(() => sendJson<Ok>('PATCH', channelPath(bot.id, channel.channel_id), body)))) recording.drop()
  }

  if (channel.recorder === undefined) return null // a nest from before 问题记录
  return (
    <div className="space-y-1">
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <Switch
          checked={recording.value}
          disabled={busy}
          onCheckedChange={(next) => void toggle(next)}
          aria-describedby={`${id}-hint`}
        />
        问题记录官
      </label>
      <p id={`${id}-hint`} className="text-xs text-muted-foreground">
        {recording.value
          ? '每小时用这个 bot 的模型看一遍群里的新消息，把 bot 没解决的问题、答错的、故障、想要的功能和意见记到「问题记录」页，按设定的时间私聊汇总给你。有人让它「记一下」它会答应。即使它在这个群关着、只看不说，也照样记'
          : '关闭：不记。每个群只能有一个记录官，打开这里会换掉原来那个'}
      </p>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/** One group (or private chat) of the bot: the on/off switch and a summary, unfolding to 发言 (活跃度, 冷却,
 * 人设) and 回复方式 (发表情 / 用黑话, 答题) — each saved on its own. Folded rows keep their controls mounted,
 * so an unsaved persona edit survives folding. A private chat has no 活跃度, 发表情 or 黑话. */
export function ChannelRow({
  bot,
  channel,
  personas,
  tones,
  reload,
}: {
  bot: Bot
  channel: BotChannel
  personas: Persona[] | null
  tones: Tone[] | null
  reload: () => void
}) {
  const { busy, error, run } = useAction(reload)
  const enabled = useJustSaved(channel.enabled)
  const { open, toggle } = useRememberedOpen(`nest.bots.channel.${bot.id}.${channel.channel_id}`, false)
  const bodyId = useId()
  const group = groupName(channel)
  const privateChat = isPrivate(channel)

  const setEnabled = async (next: boolean) => {
    const where = privateChat ? `回 ${group}` : `在 ${group} 发言`
    if (!next && bot.mode === 'live' && !window.confirm(`关掉后 ${bot.name} 不会再${where}。继续？`)) return
    enabled.mark(next)
    const body: ChannelPatch = { enabled: next }
    if (!(await run(() => sendJson<Ok>('PATCH', channelPath(bot.id, channel.channel_id), body)))) enabled.drop()
  }

  return (
    <li className={cn('rounded-xl border bg-surface transition-colors', open && 'bg-card shadow-[var(--shadow-lift)]')}>
      <div className="flex items-start gap-3 px-3 py-2.5">
        <Switch
          className="mt-0.5"
          checked={enabled.value}
          disabled={busy}
          onCheckedChange={(next) => void setEnabled(next)}
          aria-label={privateChat ? `回 ${group}` : `在 ${group} 启用`}
        />
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={bodyId}
          className="group/row flex min-w-0 flex-1 items-start gap-2 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
            <span className={cn('font-medium', !enabled.value && 'text-muted-foreground')}>{group}</span>
            <Summary channel={channel} />
          </span>
          <span className="mt-0.5 flex shrink-0 items-center gap-1 text-xs text-muted-foreground group-hover/row:text-foreground">
            {open ? '收起' : '设置'}
            <ChevronDown
              aria-hidden
              className={cn('size-4 transition-transform duration-200 motion-reduce:transition-none', !open && '-rotate-90')}
            />
          </span>
        </button>
      </div>
      {error && (
        <p role="alert" className="px-3 pb-2 text-xs text-destructive">
          {error}
        </p>
      )}
      <div id={bodyId} hidden={!open} className="space-y-5 border-t px-3 pt-3 pb-4">
        <Part title="发言">
          {!privateChat && !can(bot, 'list_channels') && <NameControl channel={channel} reload={reload} />}
          {!privateChat && can(bot, 'sees_all') && <ActivityControl bot={bot} channel={channel} reload={reload} />}
          {!privateChat && !can(bot, 'sees_all') && (
            <p className="text-xs text-muted-foreground">这个平台的 bot 只收得到 @ 它的话，所以只回点名。</p>
          )}
          <CooldownControl bot={bot} channel={channel} reload={reload} />
          <PersonaControls bot={bot} channel={channel} personas={personas} reload={reload} />
          <ToneSelect bot={bot} channel={channel} tones={tones} which="chat" reload={reload} />
        </Part>
        <Part title="回复方式">
          <ExtrasControls bot={bot} channel={channel} reload={reload} />
          <SplitControls bot={bot} channel={channel} reload={reload} />
          <AnswerControl bot={bot} channel={channel} tones={tones} reload={reload} />
        </Part>
        {!privateChat && channel.recorder !== undefined && can(bot, 'sees_all') && (
          <Part title="记录">
            <RecorderControl bot={bot} channel={channel} reload={reload} />
          </Part>
        )}
      </div>
    </li>
  )
}
