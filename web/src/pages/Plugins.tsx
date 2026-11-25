import { useCallback, useEffect, useRef, useState } from 'react'
import { PackagePlus, Puzzle, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { getJson, sendBlob, sendJson } from '@/lib/api'
import { cn } from '@/lib/utils'

/** 插件: nest's own tools and the plugins installed — on or off, and for which companions; install a plugin
 * zip after seeing what it provides and asks for. */

type Trust = 'builtin' | 'content' | 'guides' | 'code' | 'trusted'
type Permissions = { network: string[]; secrets: { name: string; label: string }[]; confirm: string[] }
type Plugin = {
  id: string
  name: string
  version: string
  author: string
  description: string
  kinds: string[]
  trust: Trust
  tools?: string[]
  permissions: Permissions
  enabled: boolean
  bots: string[] | null
  skills?: { name: string; description: string; text?: string }[]
  pets?: PetItem[]
  themes?: { name: string; description: string }[]
  secrets_set?: Record<string, boolean>
  mcp_tools?: string[] | null
  mcp_problem?: string
  code?: { added?: string[]; problem?: string } | null
}
type PetItem = { path: string; kind: 'pet' | 'skin' | 'accessory'; for: string; label: string; preview: string; frame: number }
type Listing = {
  plugins: Plugin[]
  broken: Record<string, string[]>
  bots: { bot_id: string; name: string }[]
  can_run: boolean
}
type Staged = { stage: string; plugin: Plugin; upgrade: string | null }

const KIND: Record<string, string> = {
  tools: '工具',
  skills: '技能',
  pets: '桌宠',
  stickers: '表情包',
  themes: '主题',
  mcp: 'MCP 工具',
  companion: '伙伴',
  python: 'Python 模块',
}
const TRUST: Record<Trust, { label: string; tone: string; detail: string }> = {
  builtin: { label: 'nest 自带', tone: 'text-muted-foreground', detail: '' },
  content: { label: '只有内容', tone: 'text-emerald-600 dark:text-emerald-400', detail: '没有代码，只有图片这类内容' },
  guides: {
    label: '会指挥她做事',
    tone: 'text-amber-600 dark:text-amber-400',
    detail:
      '带技能（做事的方法）或人设：她会照着做，包括跑命令、联网、把文件发出去。装之前把下面的全文读一遍，里面有你不认的要求就别装',
  },
  code: {
    label: '有代码 · 在她的容器里跑',
    tone: 'text-amber-600 dark:text-amber-400',
    detail: '它的程序装在伙伴自己的容器里跑，碰不到 nest 和这台电脑的其他地方',
  },
  trusted: {
    label: '有代码 · 在 nest 里跑',
    tone: 'text-destructive',
    detail: '它的程序直接在 nest 里跑，能碰到 nest 的一切（账号、密钥、聊天记录）。只装你完全信任的',
  },
}

export function Plugins() {
  const [listing, setListing] = useState<Listing | null>(null)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  const [staged, setStaged] = useState<Staged | null>(null)
  const [busy, setBusy] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    getJson<Listing>('/api/plugins').then(
      (data) => !cancelled && (setListing(data), setError('')),
      (e: Error) => !cancelled && setError(`读取失败：${e.message}`),
    )
    return () => {
      cancelled = true
    }
  }, [nonce])

  const act = async (work: () => Promise<unknown>) => {
    setBusy(true)
    setError('')
    try {
      await work()
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const choose = (file: File | undefined) => {
    if (!file) return
    void act(async () => setStaged(await sendBlob<Staged>('/api/plugin-packages', file)))
  }

  const builtin = listing?.plugins.filter((p) => p.trust === 'builtin') ?? []
  const installed = listing?.plugins.filter((p) => p.trust !== 'builtin') ?? []
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <p className="min-w-0 flex-1 text-sm text-muted-foreground">
          伙伴能用的工具和装上的插件。每个都能整个关掉，或者只给某几个伙伴用。
        </p>
        <input ref={picker} type="file" accept=".zip" hidden onChange={(e) => (choose(e.target.files?.[0]), (e.target.value = ''))} />
        <Button disabled={busy} onClick={() => picker.current?.click()}>
          <PackagePlus className="size-4" />
          安装插件（zip）
        </Button>
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {staged && (
        <StagedCard
          staged={staged}
          busy={busy}
          onCancel={() => {
            void sendJson('DELETE', `/api/plugin-packages/${staged.stage}`).catch(() => undefined)
            setStaged(null)
          }}
          onInstall={() =>
            void act(async () => {
              await sendJson(
                'POST',
                `/api/plugin-packages/${staged.stage}/install`,
                staged.plugin.trust === 'trusted' ? { trust: true } : undefined,
              )
              setStaged(null)
            })
          }
        />
      )}
      <Market busy={busy} onStaged={setStaged} act={act} nonce={nonce} />
      <section className="space-y-3">
        <h2 className="text-sm font-medium">装上的插件</h2>
        {listing && installed.length === 0 && <p className="text-sm text-muted-foreground">还没有装插件。</p>}
        {installed.map((p) => (
          <PluginCard key={p.id} plugin={p} bots={listing?.bots ?? []} busy={busy} act={act} canRun={listing?.can_run ?? true} />
        ))}
        {listing &&
          Object.entries(listing.broken).map(([folder, problems]) => (
            <p key={folder} className="rounded-lg border border-destructive/40 p-3 text-xs text-destructive">
              plugins/{folder} 读不了：{problems.join('；')}
            </p>
          ))}
      </section>
      <section className="space-y-3">
        <h2 className="text-sm font-medium">nest 自带</h2>
        {builtin.map((p) => (
          <PluginCard key={p.id} plugin={p} bots={listing?.bots ?? []} busy={busy} act={act} />
        ))}
      </section>
    </div>
  )
}

/** A plugin of colours only: not given to companions, picked under 系统 → 外观. */
const themesOnly = (plugin: Plugin) => plugin.kinds.length > 0 && plugin.kinds.every((k) => k === 'themes')

function Facts({ plugin }: { plugin: Plugin }) {
  const trust = TRUST[plugin.trust]
  const { network, secrets, confirm } = plugin.permissions
  return (
    <div className="space-y-1 text-xs text-muted-foreground">
      <div>
        {plugin.kinds.map((k) => KIND[k] ?? k).join('、')}
        {plugin.version && ` · ${plugin.version}`}
        {plugin.author && plugin.trust !== 'builtin' && ` · ${plugin.author}`}
        {' · '}
        <span className={trust.tone}>{trust.label}</span>
      </div>
      {plugin.tools && <div>工具：{plugin.tools.join('、')}</div>}
      {!!plugin.skills?.length && (
        <div>技能：{plugin.skills.map((k) => (k.description ? `${k.name}（${k.description}）` : k.name)).join('；')}</div>
      )}
      {!!plugin.themes?.length && (
        <div>主题：{plugin.themes.map((t) => (t.description ? `${t.name}（${t.description}）` : t.name)).join('；')}</div>
      )}
      {network.length > 0 && <div>上网：{network.includes('*') ? '任意公网网站' : network.join('、')}</div>}
      {secrets.length > 0 && <div>要你填的密钥：{secrets.map((s) => s.label).join('、')}</div>}
      {confirm.length > 0 && <div>每次要你点头的工具：{confirm.join('、')}</div>}
    </div>
  )
}

type ReachData = {
  declared: string[]
  seen: { host: string; times: number; refused: number; last: number | null; declared: boolean }[]
  undeclared: string[]
  logged: boolean
}

/** What its containers really reached (the proxy's log) against the sites it declared: opened on demand. */
function Reach({ pluginId }: { pluginId: string }) {
  const [data, setData] = useState<ReachData | null>(null)
  const [error, setError] = useState('')
  const load = () => {
    setError('')
    getJson<ReachData>(`/api/plugins/${encodeURIComponent(pluginId)}/network`).then(setData, (e: Error) =>
      setError(`读取失败：${e.message}`),
    )
  }
  return (
    <details className="text-xs" onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && !data && load()}>
      <summary className="cursor-pointer text-muted-foreground">它实际连了哪些网站</summary>
      <div className="space-y-1 pt-1">
        {error && <p role="alert" className="text-destructive">{error}</p>}
        {data && !data.logged && <p className="text-muted-foreground">这台 nest 没有联网记录。</p>}
        {data?.logged && data.seen.length === 0 && (
          <p className="text-muted-foreground">联网记录里还没有它的连接（记录只保留最近一段）。</p>
        )}
        {data && data.undeclared.length > 0 && (
          <p className="text-destructive">
            它连了没声明过的网站：{data.undeclared.join('、')}。没装错的话，可以问问作者为什么。
          </p>
        )}
        {data && data.seen.length > 0 && (
          <ul className="space-y-0.5">
            {data.seen.map((h) => (
              <li key={h.host} className={cn(!h.declared && 'text-destructive')}>
                {h.host} · {h.times} 次{h.refused ? `（被拦 ${h.refused} 次）` : ''}
                {h.declared ? '' : ' · 没声明'}
              </li>
            ))}
          </ul>
        )}
      </div>
    </details>
  )
}

/** A skill's full text, read before it is installed: it tells her what to do. */
export function SkillTexts({ skills }: { skills?: { name: string; text?: string }[] }) {
  const shown = (skills ?? []).filter((k) => k.text !== undefined)
  if (!shown.length) return null
  return (
    <div className="space-y-1">
      {shown.map((k) => (
        <details key={k.name} className="rounded border text-xs">
          <summary className="cursor-pointer px-2 py-1">技能「{k.name}」全文</summary>
          <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap break-words border-t bg-muted/40 p-2 font-sans">
            {k.text}
          </pre>
        </details>
      ))}
    </div>
  )
}

function StagedCard({ staged, busy, onCancel, onInstall }: {
  staged: Staged
  busy: boolean
  onCancel: () => void
  onInstall: () => void
}) {
  const { plugin } = staged
  const trust = TRUST[plugin.trust]
  return (
    <div className="space-y-3 rounded-xl border-2 border-primary/40 p-4">
      <div className="flex items-center gap-2">
        <Puzzle className="size-5 shrink-0" />
        <div className="min-w-0">
          <div className="font-medium">
            {staged.upgrade ? `升级「${plugin.name}」：${staged.upgrade} → ${plugin.version}` : `安装「${plugin.name}」？`}
          </div>
          <div className="text-xs text-muted-foreground">{plugin.id}</div>
        </div>
      </div>
      {plugin.description && <p className="text-sm">{plugin.description}</p>}
      <Facts plugin={plugin} />
      {trust.detail && <p className={cn('text-xs', trust.tone)}>{trust.detail}</p>}
      <SkillTexts skills={plugin.skills} />
      <p className="text-xs text-muted-foreground">
        {plugin.trust === 'trusted'
          ? '它的代码在 nest 下次启动时载入（装好后要重启 nest）。'
          : themesOnly(plugin)
            ? '装好以后在「系统」页的「外观」里选它。'
            : '装好以后先不给任何伙伴用，你在下面选给谁。'}
      </p>
      <div className="flex gap-2">
        <Button disabled={busy} variant={plugin.trust === 'trusted' ? 'destructive' : 'default'} onClick={onInstall}>
          {plugin.trust === 'trusted' ? '我信任它，安装' : staged.upgrade ? '升级' : '安装'}
        </Button>
        <Button variant="ghost" disabled={busy} onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  )
}

function PluginCard({ plugin, bots, busy, act, canRun = true }: {
  plugin: Plugin
  bots: { bot_id: string; name: string }[]
  busy: boolean
  act: (work: () => Promise<unknown>) => Promise<void>
  canRun?: boolean
}) {
  const path = `/api/plugins/${encodeURIComponent(plugin.id)}`
  const everyone = plugin.bots === null
  const chosen = new Set(plugin.bots ?? bots.map((b) => b.bot_id))
  const pick = (botId: string, on: boolean) => {
    const next = new Set(chosen)
    if (on) next.add(botId)
    else next.delete(botId)
    void act(() => sendJson('PUT', path, { bots: [...next] }))
  }
  return (
    <div className={cn('space-y-3 rounded-xl border p-4', !plugin.enabled && 'opacity-60')}>
      <div className="flex items-start gap-3">
        <Switch
          checked={plugin.enabled}
          disabled={busy}
          aria-label={plugin.enabled ? `关掉${plugin.name}` : `打开${plugin.name}`}
          onCheckedChange={(on) => void act(() => sendJson('PUT', path, { enabled: on }))}
        />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="font-medium">{plugin.name}</div>
          {plugin.description && <p className="text-sm text-muted-foreground">{plugin.description}</p>}
          <Facts plugin={plugin} />
          {plugin.kinds.includes('mcp') && <McpState plugin={plugin} canRun={canRun} />}
          {plugin.kinds.includes('mcp') && <Reach pluginId={plugin.id} />}
          {plugin.trust === 'trusted' && <CodeState plugin={plugin} />}
          {plugin.permissions.secrets.length > 0 && <Secrets plugin={plugin} busy={busy} act={act} />}
          {!!plugin.pets?.length && (
            <div className="flex flex-wrap gap-3 pt-1">
              {plugin.pets.map((item) => (
                <PetPreview key={item.path} pluginId={plugin.id} item={item} />
              ))}
            </div>
          )}
        </div>
        {plugin.trust !== 'builtin' && (
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive"
            disabled={busy}
            onClick={() => window.confirm(`卸载「${plugin.name}」？`) && void act(() => sendJson('DELETE', path))}
          >
            <Trash2 className="size-4" />
            卸载
          </Button>
        )}
      </div>
      {plugin.enabled && themesOnly(plugin) && (
        <p className="border-t pt-3 text-xs text-muted-foreground">
          主题对所有地方都一样，不分伙伴：在「系统」页的「外观」里选用。关掉它，用着它的界面会回到 nest 自己的样子。
        </p>
      )}
      {plugin.enabled && !themesOnly(plugin) && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-3 text-sm">
          <label className="flex items-center gap-2">
            <Switch
              checked={everyone}
              disabled={busy}
              onCheckedChange={(on) => void act(() => sendJson('PUT', path, on ? { all_bots: true } : { bots: [...chosen] }))}
            />
            所有伙伴
          </label>
          {!everyone &&
            bots.map((b) => (
              <label key={b.bot_id} className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={chosen.has(b.bot_id)}
                  disabled={busy}
                  onChange={(e) => pick(b.bot_id, e.target.checked)}
                />
                {b.name}
              </label>
            ))}
          {!everyone && chosen.size === 0 && <span className="text-xs text-warn">现在没有伙伴能用它</span>}
        </div>
      )}
    </div>
  )
}

const PET_KIND = { pet: '桌宠', skin: '皮肤', accessory: '配饰' } as const

/** The first frame of a pet's or skin's idle sheet, or an accessory's picture. */
function PetPreview({ pluginId, item }: { pluginId: string; item: PetItem }) {
  const [src, setSrc] = useState('')
  useEffect(() => {
    if (!item.preview) return
    let cancelled = false
    const path = `${item.path}/${item.preview}`
    getJson<{ data: string }>(`/api/pet-plugins/${encodeURIComponent(pluginId)}/file?path=${encodeURIComponent(path)}`).then(
      (got) => !cancelled && setSrc(`data:image/png;base64,${got.data}`),
      () => {},
    )
    return () => {
      cancelled = true
    }
  }, [pluginId, item.path, item.preview])
  return (
    <figure className="w-20 text-center text-[11px] text-muted-foreground">
      <div
        className="mx-auto size-16 rounded-md border bg-muted/40 bg-no-repeat [image-rendering:pixelated]"
        style={
          src
            ? item.kind === 'accessory'
              ? { backgroundImage: `url(${src})`, backgroundSize: 'contain', backgroundPosition: 'center' }
              : { backgroundImage: `url(${src})`, backgroundSize: 'auto 100%', backgroundPosition: '0 0' }
            : undefined
        }
      />
      <figcaption className="truncate pt-1">
        {PET_KIND[item.kind]} · {item.label}
        {item.for && <span className="block truncate">给 {item.for}</span>}
      </figcaption>
    </figure>
  )
}

function McpState({ plugin, canRun }: { plugin: Plugin; canRun: boolean }) {
  if (!canRun) return <p className="text-xs text-warn">这台电脑上没有 Docker，这个插件的工具用不了。</p>
  if (plugin.mcp_problem) return <p className="text-xs text-destructive">工具没读出来：{plugin.mcp_problem}</p>
  if (plugin.mcp_tools) return <p className="text-xs text-muted-foreground">它的工具：{plugin.mcp_tools.join('、') || '（没有）'}</p>
  return (
    <p className="text-xs text-muted-foreground">
      第一次给某个伙伴用时，会在她的容器里安装并读出它有哪些工具（要联网，可能要一两分钟）。
    </p>
  )
}

function Secrets({ plugin, busy, act }: {
  plugin: Plugin
  busy: boolean
  act: (work: () => Promise<unknown>) => Promise<void>
}) {
  const [values, setValues] = useState<Record<string, string>>({})
  const path = `/api/plugins/${encodeURIComponent(plugin.id)}/secrets`
  const save = (name: string, value: string) =>
    void act(async () => {
      await sendJson('PUT', path, { values: { [name]: value } })
      setValues((v) => ({ ...v, [name]: '' }))
    })
  return (
    <div className="space-y-2 pt-1">
      {plugin.permissions.secrets.map((secret) => {
        const set = plugin.secrets_set?.[secret.name]
        return (
          <div key={secret.name} className="flex flex-wrap items-center gap-2 text-xs">
            <span className="w-28 shrink-0 truncate" title={secret.name}>
              {secret.label}
            </span>
            <input
              type="password"
              autoComplete="off"
              value={values[secret.name] ?? ''}
              placeholder={set ? '已填（留着不动就不改）' : '还没填'}
              aria-label={secret.label}
              onChange={(e) => setValues((v) => ({ ...v, [secret.name]: e.target.value }))}
              className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2"
            />
            <Button size="sm" variant="outline" disabled={busy || !values[secret.name]?.trim()} onClick={() => save(secret.name, values[secret.name] ?? '')}>
              保存
            </Button>
            {set && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => save(secret.name, '')}>
                清掉
              </Button>
            )}
          </div>
        )
      })}
    </div>
  )
}

function CodeState({ plugin }: { plugin: Plugin }) {
  if (plugin.code?.problem) return <p className="text-xs text-destructive">代码没载入：{plugin.code.problem}</p>
  if (plugin.code?.added) {
    return (
      <p className="text-xs text-muted-foreground">
        已载入：{plugin.code.added.join('、') || '（什么也没加）'}。开关、升级、卸载都要重启 nest 才生效。
      </p>
    )
  }
  return <p className="text-xs text-warn">还没载入：重启 nest 后生效{plugin.enabled ? '' : '（现在是关着的）'}。</p>
}

type MarketEntry = {
  id: string
  name: string
  version: string
  author: string
  description: string
  kinds: string[]
  trust: Exclude<Trust, 'builtin'>
  size: number
  permissions: { network: string[]; secrets: string[]; confirm: string[] }
  installed: string | null
  /** The market has a later version than the one installed. Absent on an older nest. */
  newer?: boolean
  /** The nest it needs at least, and whether this one is too old for it. */
  nest?: string
  too_new?: boolean
  /** '' not signed; 'signed'; 'bad'; 'changed': not by the author of the one installed. */
  signature?: '' | 'signed' | 'bad' | 'changed'
  /** Where to say something is wrong with it. */
  report?: string
}
type MarketView = { url: string; entries: MarketEntry[]; error: string }

/** 市场: a list of plugins at an https address you set; installing one downloads it, checks it against the
 * list's checksum, and shows it like a zip you chose yourself before anything is installed. */
function Market({ busy, onStaged, act, nonce }: {
  busy: boolean
  onStaged: (staged: Staged) => void
  act: (work: () => Promise<unknown>) => Promise<void>
  nonce: number
}) {
  const [view, setView] = useState<MarketView | null>(null)
  const [address, setAddress] = useState('')
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [again, setAgain] = useState(0)

  useEffect(() => {
    let cancelled = false
    getJson<MarketView>('/api/market').then(
      (data) => {
        if (cancelled) return
        setView(data)
        setAddress(data.url)
        setLoading(false)
      },
      (e: Error) => !cancelled && (setError(e.message), setLoading(false)),
    )
    return () => {
      cancelled = true
    }
  }, [nonce, again])

  const save = () =>
    void act(async () => {
      await sendJson('PUT', '/api/market', { url: address.trim() })
      setEditing(false)
      setAgain((n) => n + 1)
    })

  return (
    <section className="space-y-3 rounded-xl border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium">插件市场</h2>
        {view?.url && !editing && (
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={view.url}>
            {view.url}
          </span>
        )}
        {!editing && (
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setEditing(true)}>
            {view?.url ? '换地址' : '设地址'}
          </Button>
        )}
      </div>
      {editing && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="https://…/plugins.json（插件清单的地址）"
            aria-label="插件市场的地址"
            className="h-9 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm"
          />
          <Button size="sm" disabled={busy} onClick={save}>
            保存
          </Button>
          <Button size="sm" variant="ghost" onClick={() => (setEditing(false), setAddress(view?.url ?? ''))}>
            取消
          </Button>
        </div>
      )}
      {!view?.url && !editing && (
        <p className="text-xs text-muted-foreground">还没有设市场地址。设一个插件清单（JSON）的 https 地址，就能在这里浏览和安装。</p>
      )}
      {loading && <p className="text-xs text-muted-foreground">正在读清单…</p>}
      {(error || view?.error) && <p role="alert" className="text-xs text-destructive">{error || view?.error}</p>}
      {view?.url && !loading && !view.error && view.entries.length === 0 && (
        <p className="text-xs text-muted-foreground">清单里没有能装的插件。</p>
      )}
      <ul className="space-y-2">
        {view?.entries.map((entry) => {
          const trust = TRUST[entry.trust]
          const newer = entry.newer ?? (entry.installed && entry.installed !== entry.version)
          const refused = entry.signature === 'bad' || entry.signature === 'changed'
          return (
            <li key={entry.id} className="flex flex-wrap items-start gap-3 rounded-lg border p-3">
              <div className="min-w-0 flex-1 space-y-1">
                <div className="font-medium">
                  {entry.name}
                  <span className="ml-2 text-xs font-normal text-muted-foreground">{entry.version}</span>
                </div>
                {entry.description && <p className="text-xs text-muted-foreground">{entry.description}</p>}
                <div className="text-xs text-muted-foreground">
                  {entry.kinds.map((k) => KIND[k] ?? k).join('、')}
                  {entry.author && ` · ${entry.author}`} · {Math.ceil(entry.size / 1024)} KB ·{' '}
                  <span className={trust.tone}>{trust.label}</span>
                  {entry.permissions.network.length > 0 && ` · 上网：${entry.permissions.network.join('、')}`}
                  {entry.signature === 'signed' && ' · 有作者签名'}
                  {entry.nest && ` · 要 nest ${entry.nest} 以上`}
                </div>
                {refused && (
                  <p className="text-xs text-destructive">
                    {entry.signature === 'bad'
                      ? '作者签名对不上，不能装。'
                      : '签名和装着的那一版不是同一个作者，不能升级。确认没问题的话，先卸载再装。'}
                  </p>
                )}
                {entry.too_new && <p className="text-xs text-warn">你的 nest 版本低了，先更新 nest。</p>}
                {entry.report && (
                  <a
                    href={entry.report}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-block text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                  >
                    举报这个插件
                  </a>
                )}
              </div>
              <Button
                size="sm"
                variant={entry.installed && !newer ? 'ghost' : 'outline'}
                disabled={busy || refused || entry.too_new || (!!entry.installed && !newer)}
                onClick={() =>
                  void act(async () =>
                    onStaged(await sendJson<Staged>('POST', `/api/market/${encodeURIComponent(entry.id)}/stage`)),
                  )
                }
              >
                {entry.installed ? (newer ? `升级到 ${entry.version}` : '已装') : '下载看看'}
              </Button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
