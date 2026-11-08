import { useId, useState, type FormEvent } from 'react'
import { Check, LoaderCircle, Plus, X } from 'lucide-react'

import { parseAliases, useAction } from '@/components/bots/shared'
import { QRLogin } from '@/components/QRLogin'
import { Segmented } from '@/components/Segmented'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { sendJson, type ConnectionTrial, type PlatformView, type ProviderView } from '@/lib/api'
import { usePlatforms } from '@/lib/live'
import { cn } from '@/lib/utils'

/** The form's fields as the platform lists them, with their defaults. */
const blank = (platform: PlatformView) => Object.fromEntries(platform.fields.map((f) => [f.name, f.default]))

const AFTER_ADDING: Record<string, string> = {
  discord: '在它的「群」里打开要去的频道，看过效果再改成正式发言。',
  wecom: '先去群里 @ 它一次，群才会出现在「在其他群启用」里；回来给群起个名字再开启。它只收得到 @ 它的话。',
}

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

/** 增加 bot: pick the platform, then its form. QQ: nest sets up the account's container on the Mac and shows the
 * login code to scan. Others connect with what is filled in here; secrets are cleared from the page once used. */
export function AddBot({ providers, reload }: { providers: ProviderView[] | null; reload: () => void }) {
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState('qq')
  const [name, setName] = useState('')
  const [qq, setQq] = useState('')
  const [aliases, setAliases] = useState('')
  const [providerId, setProviderId] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  const [trial, setTrial] = useState<ConnectionTrial | null>(null)
  const [testing, setTesting] = useState(false)
  const [scanning, setScanning] = useState<string | null>(null) // the new bot's container
  const [added, setAdded] = useState(false)
  const { busy, error, setError, run } = useAction(reload)
  const { data: platforms } = usePlatforms()
  const id = useId()
  const platform = platforms?.find((p) => p.key === key) ?? null
  const others = key !== 'qq' && platform !== null

  const close = () => {
    setOpen(false)
    setScanning(null)
    setAdded(false)
    setName('')
    setQq('')
    setAliases('')
    setProviderId('')
    setFields({})
    setTrial(null)
    setKey('qq')
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
    const ok = await run(async () => {
      const body = others
        ? { platform: key, fields }
        : { self_id: qq.trim() }
      const made = await sendJson<{ bot_id: string; container: string }>('POST', '/api/bots', {
        name: name.trim(),
        aliases: parseAliases(aliases),
        provider_id: providerId || null,
        ...body,
      })
      container = made.container
    })
    if (!ok) return
    setFields({}) // no secret stays on the page
    if (others) setAdded(true)
    else setScanning(container)
  }

  if (!open) {
    return (
      <div>
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          <Plus />
          增加 bot
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
          新 bot 先是「只看不说」：在它的「群」里打开要去的群，看过效果再改成正式发言。
        </p>
        <QRLogin container={scanning} onClose={close} />
      </section>
    )
  }
  if (added) {
    return (
      <section className="space-y-3 rounded-2xl border bg-card p-5">
        <h2 className="text-sm font-semibold">{name.trim()} 已经加好了</h2>
        <p className="text-xs text-muted-foreground">新 bot 先是「只看不说」。{AFTER_ADDING[key] ?? ''}</p>
        <Button size="sm" variant="outline" onClick={close}>
          知道了
        </Button>
      </section>
    )
  }
  const missing = others && platform.fields.some((f) => f.required && !fields[f.name]?.trim())
  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-3 rounded-2xl border bg-card p-5">
      <h2 className="text-sm font-semibold">增加 bot</h2>
      {platforms && platforms.length > 1 && (
        <Segmented
          label="平台"
          options={platforms.map((p) => ({ value: p.key, label: p.label }))}
          value={key}
          onChange={choose}
          disabled={busy}
        />
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <NameAndModel
          providers={providers}
          name={name}
          setName={setName}
          aliases={aliases}
          setAliases={setAliases}
          providerId={providerId}
          setProviderId={setProviderId}
        />
        {!others && (
          <div className="space-y-1">
            <Label htmlFor={`${id}-qq`}>QQ 号</Label>
            <Input id={`${id}-qq`} inputMode="numeric" value={qq} maxLength={12} placeholder="这个 bot 用的 QQ 号" onChange={(e) => setQq(e.target.value)} />
          </div>
        )}
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
      {!others && (
        <p className="text-xs text-muted-foreground">
          nest 会在 Mac 上给这个号建一个 QQ 容器（端口、令牌、自动登录都配好），然后在这里显示登录二维码。一个号同一时间只能在一个地方登录：如果它正在别的设备或程序里挂着，先退出。
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
        <Button type="submit" size="sm" disabled={busy || testing || !name.trim() || (others ? missing : !qq.trim())}>
          {busy && <LoaderCircle className="animate-spin" />}
          {busy ? (others ? '正在连接…' : '正在建 QQ 容器…') : '增加'}
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
