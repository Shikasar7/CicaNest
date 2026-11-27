import { useId, useState, type FormEvent } from 'react'
import { Check, LoaderCircle, Plus, X } from 'lucide-react'

import { parseAliases, useAction } from '@/components/bots/shared'
import { QRLogin } from '@/components/QRLogin'
import { Segmented } from '@/components/Segmented'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { sendJson, type Bot, type ConnectionTrial, type PlatformView, type ProviderView } from '@/lib/api'
import { usePlatforms } from '@/lib/live'
import { cn } from '@/lib/utils'

/** The form's fields as the platform lists them, with their defaults. */
const blank = (platform: PlatformView) => Object.fromEntries(platform.fields.map((f) => [f.name, f.default]))

const AFTER_ADDING: Record<string, string> = {
  discord: '在她的「群和私聊」里打开要去的频道，看过效果再改成正式发言。',
  telegram: '把它拉进群，群里说一句话，群就会出现在「在其他群启用」里；看过效果再改成正式发言。',
  slack: '在频道里 /invite 它，频道就会出现在「在其他群启用」里；看过效果再改成正式发言。',
  wecom: '先去群里 @ 它一次，群才会出现在「在其他群启用」里；回来给群起个名字再开启。它只收得到 @ 它的话。',
  qq: '在她的「群和私聊」里打开要去的群，看过效果再改成正式发言。',
}

/** How a QQ account reaches nest: a NapCat container nest makes, or a OneBot 11 program someone runs. */
type QQWay = 'container' | 'own'
const NO_OWN = { http_url: '', ws_url: '', token: '' }

function NameAndModel({
  providers,
  name,
  setName,
  aliases,
  setAliases,
  providerId,
  setProviderId,
}: {
  providers: ProviderView[] | null
  name: string
  setName: (value: string) => void
  aliases: string
  setAliases: (value: string) => void
  providerId: string
  setProviderId: (value: string) => void
}) {
  const id = useId()
  return (
    <>
      <div className="space-y-1">
        <Label htmlFor={`${id}-name`}>名字</Label>
        <Input id={`${id}-name`} value={name} maxLength={40} placeholder="比如：小海豚" onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-aliases`}>别名（叫到这些也算点名，可不填）</Label>
        <Input id={`${id}-aliases`} value={aliases} placeholder="用顿号隔开，比如：海豚、豚豚" onChange={(e) => setAliases(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-model`}>模型</Label>
        <NativeSelect id={`${id}-model`} value={providerId} onChange={(e) => setProviderId(e.target.value)}>
          <option value="">先不选</option>
          {(providers ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.id}
            </option>
          ))}
        </NativeSelect>
      </div>
    </>
  )
}

/** 增加 bot: pick the platform, then its form. QQ: nest sets up the account's container on its machine and shows the
 * login code to scan, or connects to a OneBot 11 program the person runs (NapCat's image left Docker Hub in
 * 2026-11, so a new machine may have nothing to make the container from). Others connect with what is filled in here; secrets are cleared from the page once used.
 *
 * With `companion` (one who lives only on the desk) it is 接一个平台: no name or model to ask, the account is made
 * with hers and she moves into it at once (POST /api/bots/{id}/move-in), keeping everything of hers. */
export function AddBot({
  providers,
  reload,
  companion,
  onAdded,
  onMoved,
}: {
  providers: ProviderView[] | null
  reload: () => void
  companion?: Bot
  /** A bot was added (not for a companion): its id. */
  onAdded?: (botId: string) => void
  /** The companion moved into the new account: her new id, and the QQ container to scan the login code of. */
  onMoved?: (botId: string, container: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [picked, setKey] = useState('')
  const [name, setName] = useState('')
  const [qq, setQq] = useState('')
  const [qqWay, setQqWay] = useState<QQWay>('container')
  const [own, setOwn] = useState(NO_OWN)
  const [aliases, setAliases] = useState('')
  const [providerId, setProviderId] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  const [trial, setTrial] = useState<ConnectionTrial | null>(null)
  const [testing, setTesting] = useState(false)
  const [scanning, setScanning] = useState<string | null>(null) // the new bot's container
  const [added, setAdded] = useState(false)
  const { busy, error, setError, run } = useAction(reload)
  const { data: all } = usePlatforms()
  // The desk is where everyone already is, not a platform to add.
  const platforms = all?.filter((p) => p.key !== 'desk') ?? null
  const key = picked || (platforms?.some((p) => p.key === 'qq') ? 'qq' : (platforms?.[0]?.key ?? 'qq'))
  const id = useId()
  const platform = platforms?.find((p) => p.key === key) ?? null
  const others = key !== 'qq' && platform !== null
  const ownQQ = !others && qqWay === 'own'

  const close = () => {
    setOpen(false)
    setScanning(null)
    setAdded(false)
    setName('')
    setQq('')
    setQqWay('container')
    setOwn(NO_OWN)
    setAliases('')
    setProviderId('')
    setFields({})
    setTrial(null)
    setKey('')
  }
  const choose = (next: string) => {
    setKey(next)
    setTrial(null)
    setError('')
    const chosen = platforms?.find((p) => p.key === next)
    setFields(chosen && next !== 'qq' ? blank(chosen) : {})
  }
  const set = (field: string, value: string) => {
    setFields((f) => ({ ...f, [field]: value }))
    setTrial(null)
  }
  const test = async () => {
    setTesting(true)
    setTrial(null)
    setError('')
    try {
      setTrial(await sendJson<ConnectionTrial>('POST', '/api/accounts/test', { platform: key, fields }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setTesting(false)
    }
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    let container = ''
    let made = ''
    const ok = await run(async () => {
      const body = others
        ? { platform: key, fields }
        : ownQQ
          ? { self_id: qq.trim(), onebot: own }
          : { self_id: qq.trim() }
      const done = await sendJson<{ bot_id: string; container: string | null }>('POST', '/api/bots', {
        name: companion ? companion.name : name.trim(),
        aliases: companion ? companion.aliases : parseAliases(aliases),
        provider_id: companion ? companion.provider_id : providerId || null,
        ...body,
      })
      container = done.container ?? ''
      made = done.bot_id
      if (companion) {
        try {
          await sendJson('POST', `/api/bots/${encodeURIComponent(made)}/move-in`, { companion: companion.id })
        } catch (e) {
          throw new Error(
            `号已经加好了，但${companion.name}没搬进去：${(e as Error).message}。可以在下面「搬进一个现有的号」里再试一次。`,
          )
        }
      }
    })
    if (!ok) return
    setFields({}) // no secret stays on the page
    setOwn(NO_OWN)
    if (companion) {
      onMoved?.(made, container)
      close()
      return
    }
    onAdded?.(made)
    if (container) setScanning(container)
    else setAdded(true)
  }

  if (!open) {
    return (
      <div>
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          <Plus />
          {companion ? '接一个平台' : '从号开始加'}
        </Button>
      </div>
    )
  }
  if (scanning) {
    return (
      <section className="space-y-3 rounded-2xl border bg-card p-5">
        <h2 className="text-sm font-semibold">{name.trim()} 已经加好了，扫码登录 QQ</h2>
        <p className="text-xs text-muted-foreground">
          QQ 容器 {scanning} 已经建好。用 {qq.trim()} 这个号的手机 QQ 扫下面的码；登录过一次以后，容器重启也会自己登上。
          新伙伴先是「只看不说」：在她的「群和私聊」里打开要去的群，看过效果再改成正式发言。
        </p>
        <QRLogin container={scanning} onClose={close} />
      </section>
    )
  }
  if (added) {
    return (
      <section className="space-y-3 rounded-2xl border bg-card p-5">
        <h2 className="text-sm font-semibold">{name.trim()} 已经加好了</h2>
        <p className="text-xs text-muted-foreground">新伙伴先是「只看不说」。{AFTER_ADDING[key] ?? ''}</p>
        <Button size="sm" variant="outline" onClick={close}>
          知道了
        </Button>
      </section>
    )
  }
  const missing = others
    ? platform.fields.some((f) => f.required && !fields[f.name]?.trim())
    : !qq.trim() || (ownQQ && (!own.http_url.trim() || !own.ws_url.trim()))
  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-3 rounded-2xl border bg-card p-5">
      <h2 className="text-sm font-semibold">{companion ? `给${companion.name}接一个平台` : '从一个号开始加伙伴'}</h2>
      {platforms && platforms.length > 1 && (
        <Segmented
          label="平台"
          options={platforms.map((p) => ({ value: p.key, label: p.label }))}
          value={key}
          onChange={choose}
          disabled={busy}
        />
      )}
      {!others && (
        <Segmented
          label="QQ 怎么接"
          options={[
            { value: 'container', label: 'nest 建 QQ 容器' },
            { value: 'own', label: '接我自己运行的协议端' },
          ]}
          value={qqWay}
          onChange={(way) => {
            setQqWay(way)
            setError('')
          }}
          disabled={busy}
        />
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {!companion && <NameAndModel
          providers={providers}
          name={name}
          setName={setName}
          aliases={aliases}
          setAliases={setAliases}
          providerId={providerId}
          setProviderId={setProviderId}
        />}
        {!others && (
          <div className="space-y-1">
            <Label htmlFor={`${id}-qq`}>QQ 号</Label>
            <Input id={`${id}-qq`} inputMode="numeric" value={qq} maxLength={12} placeholder={companion ? `${companion.name}用的 QQ 号` : '这个号的 QQ 号'} onChange={(e) => setQq(e.target.value)} />
          </div>
        )}
        {ownQQ &&
          (
            [
              ['http_url', 'HTTP 地址', 'http://127.0.0.1:3000', 'text'],
              ['ws_url', 'WebSocket 地址（正向）', 'ws://127.0.0.1:3001', 'text'],
              ['token', '令牌（access token）', '没设就留空', 'password'],
            ] as const
          ).map(([field, label, placeholder, type]) => (
            <div key={field} className="space-y-1">
              <Label htmlFor={`${id}-${field}`}>{label}</Label>
              <Input
                id={`${id}-${field}`}
                type={type}
                autoComplete="off"
                spellCheck={false}
                placeholder={placeholder}
                value={own[field]}
                onChange={(e) => setOwn((o) => ({ ...o, [field]: e.target.value }))}
              />
            </div>
          ))}
        {others &&
          platform.fields.map((f) => (
            <div key={f.name} className="space-y-1">
              <Label htmlFor={`${id}-${f.name}`}>{f.label}</Label>
              <Input
                id={`${id}-${f.name}`}
                type={f.secret ? 'password' : 'text'}
                autoComplete="off"
                spellCheck={false}
                value={fields[f.name] ?? ''}
                onChange={(e) => set(f.name, e.target.value)}
              />
              {f.hint && <p className="text-xs text-muted-foreground">{f.hint}</p>}
            </div>
          ))}
      </div>
      {!others && !ownQQ && (
        <p className="text-xs text-muted-foreground">
          nest 会在它所在的电脑上给这个号建一个 NapCat 容器（端口、令牌、自动登录都配好），然后在这里显示登录二维码。要先装好 Docker，而且这台电脑上要有 NapCat 的镜像：官方镜像 2026 年 11 月已经下架，没有的话在「平台接入」页填一个你能拿到的。一个号同一时间只能在一个地方登录：如果它正在别的设备或程序里挂着，先退出。
        </p>
      )}
      {ownQQ && (
        <p className="text-xs text-muted-foreground">
          你自己运行的 OneBot 11 协议端（NapCat 或其他），先在里面登录这个 QQ 号，打开 HTTP 服务和正向 WebSocket 服务，再把地址和令牌填到这里。nest 会先试着连一次：HTTP 要答得出是这个号，WebSocket 要连得上。协议端由你自己启动和更新，nest 不会动它。
        </p>
      )}
      {others && platform.probe_kicks && (
        <p className="text-xs text-muted-foreground">
          测试和保存都会用这个机器人连一次：同一个机器人只能有一条连接，别处在用的话会被顶下线。
        </p>
      )}
      {trial && (
        <p role="status" className={cn('flex items-start gap-1.5 text-xs', trial.ok ? 'text-ok' : 'text-destructive')}>
          {trial.ok ? <Check className="mt-px size-3.5 shrink-0" /> : <X className="mt-px size-3.5 shrink-0" />}
          {trial.detail}
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || testing || (!companion && !name.trim()) || missing}>
          {busy && <LoaderCircle className="animate-spin" />}
          {busy ? (others || ownQQ ? '正在连接…' : '正在建 QQ 容器…') : companion ? '接上' : '增加'}
        </Button>
        {others && (
          <Button type="button" size="sm" variant="outline" disabled={busy || testing || missing} onClick={() => void test()}>
            {testing && <LoaderCircle className="animate-spin" />}
            测试连接
          </Button>
        )}
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={close}>
          取消
        </Button>
      </div>
    </form>
  )
}
