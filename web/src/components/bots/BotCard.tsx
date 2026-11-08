import { useState, type CSSProperties } from 'react'
import { Activity, Camera, Cpu, MessageCircle, Users } from 'lucide-react'

import { AddGroup } from '@/components/bots/AddGroup'
import { AddPrivate } from '@/components/bots/AddPrivate'
import { AvatarPicker } from '@/components/bots/AvatarPicker'

import { BotForm } from '@/components/bots/BotForm'
import { ChannelRow } from '@/components/bots/ChannelRow'
import { ModelPanel } from '@/components/bots/ModelPanel'
import { Section } from '@/components/bots/Section'
import { BotGlyph } from '@/components/BotGlyph'
import { MODES, OUTCOMES, botForm, isPrivate, useAction } from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { sendJson, type Bot, type BotChannel, type BotMode, type Ok, type Persona, type ProviderView, type Tone } from '@/lib/api'
import { useDraft, useEditFocus } from '@/lib/drafts'
import { formatAgo } from '@/lib/format'
import { useLive, usePlatforms } from '@/lib/live'
import { botLook } from '@/lib/persona'
import { platformOf } from '@/lib/platforms'
import { cn } from '@/lib/utils'

function confirmLive(bot: Bot): boolean {
  return window.confirm(
    `让「${bot.name}」真的在群里发言？\n\n` +
      '请先确认别的程序已经不再用这个号回复，否则两边会重复回复。' +
      '随时可以点「暂停」或总览页的「全部暂停」停下来。',
  )
}

/** The chosen mode's colour: the same for every bot, so a colour always means the same state. */
const MODE_TEXT: Record<BotMode, string> = { live: 'text-ok', observe: 'text-foreground', paused: 'text-warn' }

/** "2 个群，1 个关着" / "1 个人". */
function countText(channels: BotChannel[], unit: string): string {
  const off = channels.filter((c) => !c.enabled).length
  return `${channels.length} ${unit}` + (off ? `，${off} 个关着` : '')
}

function ForeignWarning({ bot }: { bot: Bot }) {
  if (!bot.foreign_at) return null // the backend only reports sightings from the last 30 minutes
  return (
    <p
      className={cn(
        'rounded-md border px-3 py-2 text-xs',
        bot.mode === 'live' ? 'border-destructive/40 bg-destructive/10 text-destructive' : 'border-amber-500/40 bg-amber-500/10',
      )}
    >
      {formatAgo(bot.foreign_at)}有别的程序用这个号发过言（可能是别的 bot 程序 还在运行）。
      {bot.mode === 'live' ? '现在是正式发言，两边可能会重复回复。' : '现在是只看不说，不影响。'}
    </p>
  )
}

export function BotCard({
  bot,
  providers,
  personas,
  tones,
  reload,
}: {
  bot: Bot
  providers: ProviderView[] | null
  personas: Persona[] | null
  tones: Tone[] | null
  reload: () => void
}) {
  const { busy, error, run } = useAction(reload)
  const draft = useDraft(botForm(bot))
  const { firstField, editButton } = useEditFocus<HTMLInputElement>(draft.editing)
  const setMode = (mode: BotMode) => run(() => sendJson<Ok>('PATCH', `/api/bots/${encodeURIComponent(bot.id)}`, { mode }))
  const names = [bot.name, ...bot.aliases].join('、')
  const { accounts } = useLive()
  const { data: platforms } = usePlatforms()
  const platform = platformOf(bot, platforms)
  const status = accounts.find((a) => a.id === bot.id)?.status
  const online = status?.state === 'connected'
  const [picking, setPicking] = useState(false)
  const groups = bot.channels.filter((c) => !isPrivate(c))
  const privates = bot.channels.filter(isPrivate)
  const modeLabel = MODES.find((m) => m.mode === bot.mode)?.label ?? bot.mode
  return (
    <Card style={{ '--hue': botLook(bot.name, bot.id).hue } as CSSProperties}>
      <CardHeader>
        <CardTitle className="flex items-center gap-3 break-all">
          <button
            type="button"
            onClick={() => setPicking((p) => !p)}
            aria-expanded={picking}
            aria-label={`换${bot.name}的头像`}
            title="换头像"
            className="group/avatar relative shrink-0 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-[var(--hue)] focus-visible:ring-offset-2 focus-visible:ring-offset-card"
          >
            <BotGlyph name={bot.name} id={bot.id} online={online} />
            <span
              aria-hidden
              className={cn(
                'absolute -right-1 -bottom-1 grid size-[18px] place-items-center rounded-full bg-card text-muted-foreground shadow-[var(--shadow-lift)] ring-1 ring-border transition-[color,scale] duration-200 group-hover/avatar:scale-110 group-hover/avatar:text-[var(--hue)]',
                picking && 'text-[var(--hue)]',
              )}
            >
              <Camera className="size-2.5" />
            </span>
          </button>
          {bot.name}
        </CardTitle>
        <CardDescription className="break-words">
          {platform?.label ?? 'QQ'} {bot.self_id}，叫到这些名字算点名：{names}
          {status?.state === 'deaf' && <span className="mt-1 block text-destructive">{status.detail}</span>}
        </CardDescription>
        {!draft.editing && (
          <CardAction>
            <Button ref={editButton} size="sm" variant="outline" onClick={draft.start}>
              编辑
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="space-y-5">
        {picking && <AvatarPicker bot={bot} onDone={() => setPicking(false)} />}
        {draft.editing && (
          <BotForm bot={bot} providers={providers} draft={draft} firstField={firstField} onSaved={reload} />
        )}
        <ForeignWarning bot={bot} />
        <Section id={`${bot.id}.state`} title="状态" Icon={Activity} summary={`${modeLabel}，今天发了 ${bot.today.replied ?? 0} 条`}>
          <div className="space-y-4">
            <div className="space-y-2">
              <div role="radiogroup" aria-label={`${bot.name} 的模式`} className="inline-flex rounded-lg bg-muted p-0.5">
                {MODES.map((m) => {
                  const current = bot.mode === m.mode
                  return (
                    <button
                      key={m.mode}
                      type="button"
                      role="radio"
                      aria-checked={current}
                      disabled={busy || current || (m.mode === 'live' && !bot.provider)}
                      onClick={() => (m.mode !== 'live' || confirmLive(bot)) && void setMode(m.mode)}
                      className={cn(
                        'rounded-md px-3 py-1 text-sm transition-all duration-200 disabled:cursor-default',
                        current
                          ? cn('bg-card font-medium shadow-[var(--shadow-lift)]', MODE_TEXT[m.mode])
                          : 'text-muted-foreground enabled:hover:text-foreground disabled:opacity-40',
                      )}
                    >
                      {m.label}
                    </button>
                  )
                })}
              </div>
              <p className="text-xs text-muted-foreground">{MODES.find((m) => m.mode === bot.mode)?.hint}</p>
              {bot.mode !== 'live' && !bot.provider && (
                <p className="text-xs text-muted-foreground">没有可用的模型，不能正式发言：先点「编辑」选一个模型。</p>
              )}
              {error && (
                <p role="alert" className="text-xs text-destructive">
                  {error}
                </p>
              )}
            </div>
            <dl className="grid grid-cols-3 gap-2 sm:grid-cols-5">
              {OUTCOMES.map((o) => (
                <div key={o.key} className="flex flex-col-reverse rounded-lg bg-surface px-2.5 py-2">
                  <dt className="text-xs text-muted-foreground">今天{o.label}</dt>
                  <dd
                    className={cn(
                      'text-lg font-semibold tabular-nums',
                      o.key === 'failed' && (bot.today.failed ?? 0) > 0 && 'text-destructive',
                    )}
                  >
                    {bot.today[o.key] ?? 0}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </Section>
        <Section id={`${bot.id}.model`} title="模型" Icon={Cpu} summary={bot.provider_id ?? '还没选'}>
          {/* Keyed by model: a 测试一下 result belongs to the model it tested. */}
          <ModelPanel key={bot.provider_id ?? ''} bot={bot} />
        </Section>
        <Section id={`${bot.id}.groups`} title="群" Icon={Users} summary={countText(groups, '个群')}>
          <div className="space-y-2">
            {groups.length === 0 ? (
              <p className="py-2 text-sm text-muted-foreground">还没有在任何群启用</p>
            ) : (
              <ul className="space-y-2">
                {groups.map((channel) => (
                  <ChannelRow key={channel.channel_id} bot={bot} channel={channel} personas={personas} tones={tones} reload={reload} />
                ))}
              </ul>
            )}
            <AddGroup bot={bot} personas={personas} reload={reload} />
          </div>
        </Section>
        <Section id={`${bot.id}.private`} title="私聊" Icon={MessageCircle} summary={countText(privates, '个人')}>
          <div className="space-y-2">
            {privates.length === 0 ? (
              <p className="py-2 text-sm text-muted-foreground">不回任何人的私聊</p>
            ) : (
              <ul className="space-y-2">
                {privates.map((channel) => (
                  <ChannelRow key={channel.channel_id} bot={bot} channel={channel} personas={personas} tones={tones} reload={reload} />
                ))}
              </ul>
            )}
            <AddPrivate bot={bot} personas={personas} reload={reload} />
          </div>
        </Section>
      </CardContent>
    </Card>
  )
}
