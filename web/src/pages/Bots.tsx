import { useEffect, useState, type CSSProperties } from 'react'
import { Plus } from 'lucide-react'

import { AddBot } from '@/components/bots/AddBot'
import { BotCard } from '@/components/bots/BotCard'
import { BotGlyph } from '@/components/BotGlyph'
import { NewCompanion } from '@/components/chat/NewCompanion'
import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { QRLogin } from '@/components/QRLogin'
import { Button } from '@/components/ui/button'
import type { Bot } from '@/lib/api'
import { useBots, useLive, usePersonas, useProviders, useTones } from '@/lib/live'
import { botLook } from '@/lib/persona'
import { platformName } from '@/lib/platforms'
import { useConfirmLeave } from '@/lib/unsaved'
import { cn } from '@/lib/utils'

/** A list the page can do without for a moment (the model and persona pickers fall back to /api/bots). */
function SideError({ what, error, loaded }: { what: string; error: string; loaded: boolean }) {
  if (!error) return null
  return (
    <p role="alert" className="text-sm text-destructive">
      读取{what}失败{loaded ? '，下面用的是上次的数据' : ''}：{error}
    </p>
  )
}

const NEW = '@new'

/** '#/bots?bot=…': whom the address points at. */
const wantedOf = () => new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('bot')

function show(botId: string) {
  // The address follows the choice, so a reload or a link from 首页 keeps it; no hashchange, no remount.
  history.replaceState(null, '', `#/bots?bot=${encodeURIComponent(botId)}`)
}

function Row({ bot, current, onPick }: { bot: Bot; current: boolean; onPick: () => void }) {
  const { accounts } = useLive()
  const deskOnly = bot.platform === 'desk'
  const up = accounts.find((a) => a.id === bot.id)?.status.state === 'connected'
  const online = bot.mode !== 'paused' && (deskOnly || up)
  return (
    <button
      type="button"
      onClick={onPick}
      aria-current={current ? 'true' : undefined}
      style={{ '--hue': botLook(bot.name, bot.id).hue } as CSSProperties}
      className={cn(
        'flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors',
        current ? 'bg-card shadow-[var(--shadow-lift)]' : 'hover:bg-card/60',
      )}
    >
      <BotGlyph name={bot.name} id={bot.id} online={online} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{bot.name}</span>
        <span className="block truncate text-xs text-muted-foreground">
          {deskOnly ? '桌面' : `桌面 · ${platformName(bot.platform ?? 'qq')}${up ? '' : '（没连上）'}`}
          {bot.mode === 'paused' && ' · 暂停中'}
        </span>
      </span>
    </button>
  )
}

/** 添伙伴: the same three ways as in 聊天 (write one, bring one in, a sample), and for whoever already has a
 * bot account, starting from the account. */
function Adding({ bots, providers, reload, onDone, onCancel }: {
  bots: Bot[]
  providers: ReturnType<typeof useProviders>['data']
  reload: () => void
  onDone: (botId: string) => void
  onCancel: () => void
}) {
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex min-h-[28rem] flex-col overflow-hidden rounded-2xl border bg-card">
        <NewCompanion
          own={bots.length > 0}
          targets={bots.map((b) => ({ bot_id: b.id, name: b.name }))}
          onCancel={onCancel}
          onDone={(id) => {
            reload()
            onDone(id)
          }}
        />
      </div>
      <div className="space-y-2 rounded-2xl border border-dashed p-5">
        <p className="text-sm text-muted-foreground">
          已经有一个 QQ、Discord 或企业微信的 bot 号，想直接从号开始？先加号，再给她起名字、写人设。
        </p>
        <AddBot providers={providers} reload={reload} onAdded={onDone} />
      </div>
    </div>
  )
}

/** 伙伴: everyone in a list on the left, the one chosen on the right. Where she can be reached (the desk, a
 * platform) is one of her tabs, not what the page is about. */
export function Bots() {
  const bots = useBots<Bot[]>('/api/bots')
  const providers = useProviders()
  const personas = usePersonas()
  const tones = useTones()
  const confirmLeave = useConfirmLeave()
  const [selected, setSelected] = useState<string | null>(wantedOf)
  // Just connected to QQ: the login code to scan, shown with her until closed.
  const [scan, setScan] = useState<{ botId: string; container: string } | null>(null)
  useEffect(() => {
    const follow = () => {
      const wanted = wantedOf()
      if (wanted) setSelected(wanted)
    }
    window.addEventListener('hashchange', follow)
    return () => window.removeEventListener('hashchange', follow)
  }, [])
  const reload = () => {
    bots.reload()
    personas.reload()
  }
  const pick = (botId: string) => {
    if (botId === selected || !confirmLeave()) return
    setSelected(botId)
    if (botId !== NEW) show(botId)
  }
  if (!bots.data) return <FirstLoad error={bots.error} what="伙伴" />
  const list = bots.data
  const adding = selected === NEW || list.length === 0
  const bot = adding ? null : (list.find((b) => b.id === selected) ?? list[0])
  return (
    <div className="flex flex-col gap-4">
      <StaleNotice error={bots.error} />
      <SideError what="模型列表" error={providers.error} loaded={providers.data !== null} />
      <SideError what="人设列表" error={personas.error} loaded={personas.data !== null} />
      <div className="grid items-start gap-6 md:grid-cols-[14rem_minmax(0,1fr)]">
        <nav aria-label="伙伴" className="flex flex-col gap-1 md:sticky md:top-0">
          {list.map((b) => (
            <Row key={b.id} bot={b} current={b.id === bot?.id} onPick={() => pick(b.id)} />
          ))}
          <Button variant={adding ? 'default' : 'outline'} size="sm" className="mt-2 w-full justify-start" onClick={() => pick(NEW)}>
            <Plus />
            添伙伴
          </Button>
        </nav>
        {adding || !bot ? (
          <Adding
            bots={list}
            providers={providers.data}
            reload={reload}
            onCancel={() => list[0] && pick(list[0].id)}
            onDone={(id) => {
              setSelected(id)
              show(id)
            }}
          />
        ) : (
          <div className="flex min-w-0 flex-col gap-4">
            {scan?.botId === bot.id && scan.container && (
              <section className="space-y-3 rounded-2xl border bg-card p-5">
                <h2 className="text-sm font-semibold">{bot.name}接上 QQ 了，扫码登录</h2>
                <p className="text-xs text-muted-foreground">
                  QQ 容器 {scan.container} 已经建好。用 {bot.self_id} 这个号的手机 QQ 扫下面的码；登录过一次以后，容器重启也会自己登上。
                  然后在「群和私聊」里打开要去的群。
                </p>
                <QRLogin container={scan.container} onClose={() => setScan(null)} />
              </section>
            )}
            <BotCard
              key={bot.id}
              bot={bot}
              providers={providers.data}
              personas={personas.data}
              tones={tones.data}
              bots={list}
              reload={reload}
              onMoved={(id, container) => {
                setSelected(id)
                show(id)
                if (container) setScan({ botId: id, container })
              }}
            />
          </div>
        )}
      </div>
    </div>
  )
}
