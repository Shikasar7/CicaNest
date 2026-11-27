import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Download, File, Folder, Trash2, Upload } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import { fetchBytes, getJson, sendBlob, sendJson } from '@/lib/api'
import { tooBig, viewOf, type FileView } from '@/lib/file-view'
import { desktop } from '@/lib/desktop'
import { formatTime } from '@/lib/format'
import { useLive } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'

/** 文件: her own folder on nest's machine. What she makes for you is in 给你的; what you send her goes into 收到的;
 * folders on that machine you share with her are under 共享. Her commands run in a container of hers that sees
 * only this folder (and the shared ones, read-only). The page also says what keeps her from files at all: a model
 * that calls no tools, the plugin off, or (in the desktop app) 电脑上做事 not given to her. */

type Entry = { name: string; folder: boolean; size: number; modified: number }
type Listing = {
  path: string
  entries: Entry[]
  used: number
  limit: number
  for_you: string
  received: string
  can_run: boolean
  online: boolean
  shared_name: string
  old_name: string
  shared: Shared[]
  access: Access
}
type Shared = { name: string; path: string; writable: boolean }
/** What she can reach: her model calls tools (`switch`: an OpenAI-compatible one with 工具调用 off), her file
 * tools are on, 电脑上做事 is on for her (`pc`: a PC is connected for it). */
type Access = { tools: boolean; switch: boolean; model: string; files: boolean; windows: boolean; pc: boolean }
type Connection = { at: number; host: string; port: number; ok: boolean; why: string }

function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(1)} GB`
}

const join = (folder: string, name: string) => (folder ? `${folder}/${name}` : name)

export function Files({ botId, name, onClose }: { botId: string; name: string; onClose: () => void }) {
  const { subscribe } = useLive()
  const [path, setFolder] = useState('给你的')
  const [open, setOpen] = useState<Entry | null>(null) // the file being looked at, in `path`
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0) // what she changed: the file being looked at is read again
  const [busy, setBusy] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const setPath = (folder: string) => {
    setFolder(folder)
    setOpen(null)
  }
  const base = `/api/workspaces/${encodeURIComponent(botId)}`
  const { data: listing, error: unread, reload: again } = useLiveData<Listing>(
    `${base}/files?path=${encodeURIComponent(path)}`,
    [],
  )
  const reload = useCallback(() => {
    again()
    setNonce((n) => n + 1)
  }, [again])
  useEffect(() => subscribe((item) => item.kind === 'workspace' && item.botId === botId && reload()), [subscribe, botId, reload])

  const act = async (work: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true)
    setError('')
    try {
      await work()
      reload()
      return true
    } catch (e) {
      setError((e as Error).message)
      return false
    } finally {
      setBusy(false)
    }
  }

  const upload = (files: FileList | null) => {
    const chosen = files ? Array.from(files) : []
    if (!chosen.length) return
    void act(async () => {
      for (const file of chosen) await sendBlob(`${base}/upload?name=${encodeURIComponent(file.name)}`, file)
      setPath(listing?.received ?? '收到的')
    })
  }

  const crumbs = path ? path.split('/') : []
  // In a shared folder nothing is deleted from here (unshare it instead).
  const sharedName = listing?.shared_name ?? '共享'
  const inShared = path === sharedName || path.startsWith(`${sharedName}/`)
  const href = (file: string) => `${base}/download?path=${encodeURIComponent(join(path, file))}`
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <div className="min-w-0">
          <div className="font-medium">{name}的文件</div>
          <div className="text-xs text-muted-foreground">
            她在 nest 那台电脑上自己的文件夹。做好给你的东西在「给你的」里；你发给她的放进「收到的」；共享给她的文件夹在「共享」里。
          </div>
        </div>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose}>
          回到聊天
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-1 border-b px-4 py-2 text-sm">
        <button type="button" className="rounded px-1.5 py-0.5 hover:bg-muted" onClick={() => setPath('')}>
          她的文件夹
        </button>
        {crumbs.map((part, i) => (
          <span key={i} className="flex items-center gap-1">
            <span className="text-muted-foreground">/</span>
            <button
              type="button"
              className="rounded px-1.5 py-0.5 hover:bg-muted"
              onClick={() => setPath(crumbs.slice(0, i + 1).join('/'))}
            >
              {part}
            </button>
          </span>
        ))}
        <input ref={picker} type="file" multiple hidden onChange={(e) => (upload(e.target.files), (e.target.value = ''))} />
        <Button size="sm" variant="outline" className="ml-auto" disabled={busy} onClick={() => picker.current?.click()}>
          <Upload className="size-4" />
          发文件给她
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 text-sm">
        {error && <p role="alert" className="pb-3 text-destructive">{error}</p>}
        {unread && <p role="alert" className="pb-3 text-destructive">{unread}</p>}
        {!open && !listing && !unread && <p className="text-muted-foreground">正在读取……</p>}
        {!open && listing && <Reach access={listing.access} />}
        {open && (
          <Viewer
            key={join(path, open.name)}
            file={open}
            href={href(open.name)}
            nonce={nonce}
            onBack={() => setOpen(null)}
          />
        )}
        {!open && listing?.entries.length === 0 && <p className="text-muted-foreground">这里还是空的。</p>}
        <ul className="divide-y rounded-lg border" hidden={open !== null || !listing?.entries.length}>
          {listing?.entries.map((e) => (
            <li key={e.name} className="flex items-center gap-3 px-3 py-2">
              {e.folder ? <Folder className="size-4 shrink-0 text-muted-foreground" /> : <File className="size-4 shrink-0 text-muted-foreground" />}
              {e.folder ? (
                <button type="button" className="min-w-0 flex-1 truncate text-left hover:underline" onClick={() => setPath(join(path, e.name))}>
                  {e.name}
                </button>
              ) : (
                <button
                  type="button"
                  className="min-w-0 flex-1 truncate text-left hover:underline"
                  title={e.name}
                  onClick={() => setOpen(e)}
                >
                  {e.name}
                </button>
              )}
              <span className="shrink-0 text-xs text-muted-foreground">
                {!e.folder && `${bytes(e.size)} · `}
                {formatTime(e.modified)}
              </span>
              {!e.folder && (
                <a
                  href={href(e.name)}
                  download={e.name}
                  className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label={`下载 ${e.name}`}
                >
                  <Download className="size-4" />
                </a>
              )}
              {!inShared && !(path === '' && [listing.for_you, listing.received, sharedName].includes(e.name)) && (
                <button
                  type="button"
                  disabled={busy}
                  className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive"
                  aria-label={`删除 ${e.name}`}
                  onClick={() =>
                    window.confirm(`删除「${e.name}」？删了就没了。`) &&
                    void act(() => sendJson('DELETE', `${base}/files?path=${encodeURIComponent(join(path, e.name))}`))
                  }
                >
                  <Trash2 className="size-4" />
                </button>
              )}
            </li>
          ))}
        </ul>
        {!open && listing && (
          <p className="pt-3 text-xs text-muted-foreground">
            用了 {bytes(listing.used)} / {bytes(listing.limit)}
            {listing.can_run ? ' · 她的命令在只看得到这个文件夹的容器里跑' : ' · 这台电脑上没有 Docker，她现在只能读写文件、跑不了命令'}
          </p>
        )}
        {!open && listing && (
          <SharedFolders
            shared={listing.shared}
            oldName={listing.old_name}
            busy={busy}
            onSave={(folders) =>
              act(() =>
                sendJson('PUT', `${base}/shared`, { folders: folders.map((f) => ({ path: f.path, writable: f.writable })) }),
              )
            }
          />
        )}
        {!open && listing?.can_run && (
          <Network
            base={base}
            online={listing.online}
            onSwitch={(on) => void act(() => sendJson('PUT', `${base}/online`, { online: on }))}
            busy={busy}
          />
        )}
      </div>
    </div>
  )
}

/** What keeps her from files, if anything, and where to change it. */
function Reach({ access }: { access: Access }) {
  const box = 'mb-3 space-y-1.5 rounded-lg border border-warn/40 bg-warn/10 p-3 text-xs'
  if (!access.tools) {
    const model = access.model ? `「${access.model}」` : ''
    return (
      <div role="status" className={box}>
        <p className="font-medium">她现在的模型{model}不能调用工具</p>
        <p>
          读写文件、读网页、设提醒她都做不了，只能你把内容发给她。
          {access.switch ? (
            <>
              到 <a href="#/models" className="underline">「模型」页</a> 编辑这个模型，打开「工具调用」（接口本身也要支持）。
            </>
          ) : (
            <>
              给她换一个能用工具的模型：Claude、Gemini、ChatGPT 订阅，或开了「工具调用」的 OpenAI 兼容模型。
            </>
          )}
        </p>
      </div>
    )
  }
  if (!access.files) {
    return (
      <div role="status" className={box}>
        <p>
          「工作区文件」插件没给她开，这里的文件她碰不到。到 <a href="#/plugins" className="underline">「插件」页</a> 勾上她。
        </p>
      </div>
    )
  }
  if (!desktop) return null
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border p-3 text-xs text-muted-foreground">
      {access.windows ? (
        <>
          <span className="min-w-0 flex-1">
            「电脑上做事」开着：她能直接看你授权的文件夹，设成「改不用问」的文件夹改文件也不用每次点头。
            {!access.pc && '（桌面端现在没连上 nest）'}
          </span>
          {desktop.openExecutor && (
            <Button size="sm" variant="outline" onClick={() => void desktop?.openExecutor?.()}>
              授权的文件夹…
            </Button>
          )}
        </>
      ) : (
        <span>
          想让她直接碰这台电脑上任何位置的文件、跑命令：到 <a href="#/plugins" className="underline">「插件」页</a>
          把「电脑上做事」勾给她。
        </span>
      )}
    </div>
  )
}

/** 共享给她的文件夹: folders on nest's machine she reads (and, when 能改, writes) under 共享/. */
function SharedFolders({ shared, oldName, busy, onSave }: {
  shared: Shared[]
  oldName: string
  busy: boolean
  onSave: (folders: { path: string; writable: boolean }[]) => Promise<boolean>
}) {
  const [adding, setAdding] = useState('')
  const [writable, setWritable] = useState(false)
  const sure = (path: string) =>
    window.confirm(`让她能写、能改「${path}」里的文件？\n改之前原来的文件会存进她的「${oldName}」；她不会删里面的东西。`)
  const add = async () => {
    const path = adding.trim()
    if (!path || (writable && !sure(path))) return
    if (await onSave([...shared, { path, writable }])) {
      setAdding('')
      setWritable(false)
    }
  }
  const change = (folder: Shared, next: boolean) => {
    if (next && !sure(folder.path)) return
    void onSave(shared.map((f) => (f.path === folder.path ? { ...f, writable: next } : f)))
  }
  return (
    <div className="mt-4 space-y-2 rounded-lg border p-3">
      <div className="font-medium">共享给她的文件夹</div>
      <p className="text-xs text-muted-foreground">
        nest 那台电脑上的文件夹，不用再一个个发给她。她在「共享」里直接读；设成「能改」的也能写和改，改之前原来的文件会存进她的「{oldName}」。她不会删里面的东西，她跑的命令也只能读。
      </p>
      {shared.length > 0 && (
        <ul className="divide-y rounded-md border">
          {shared.map((f) => (
            <li key={f.path} className="flex items-center gap-2 px-2 py-1.5 text-xs">
              <span className="min-w-0 flex-1 truncate font-mono" title={f.path}>
                {f.path}
              </span>
              <NativeSelect
                className="h-7 text-xs"
                aria-label={`她在 ${f.path} 里能做什么`}
                value={f.writable ? 'write' : 'read'}
                disabled={busy}
                onChange={(e) => change(f, e.target.value === 'write')}
              >
                <option value="read">只能看</option>
                <option value="write">能改</option>
              </NativeSelect>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => void onSave(shared.filter((x) => x.path !== f.path))}
              >
                取消共享
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void add()
        }}
      >
        <Input
          className="h-8 min-w-48 flex-1 text-xs"
          aria-label="要共享的文件夹的完整路径"
          placeholder={'完整路径，比如 D:\\资料 或 /Users/你/Documents/资料'}
          spellCheck={false}
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
        />
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={writable} onChange={(e) => setWritable(e.target.checked)} />
          能改
        </label>
        <Button type="submit" size="sm" variant="outline" disabled={busy || !adding.trim()}>
          共享
        </Button>
      </form>
    </div>
  )
}

/** One file, looked at here: text and pictures; anything else is downloaded. Read again when she changes it. */
function Viewer({ file, href, nonce, onBack }: { file: Entry; href: string; nonce: number; onBack: () => void }) {
  const [view, setView] = useState<FileView | null>(null)
  const [error, setError] = useState('')
  const big = tooBig(file.name, file.size)
  useEffect(() => {
    if (big) return
    const stop = new AbortController()
    fetchBytes(href, stop.signal)
      .then((bytes) => viewOf(file.name, bytes))
      .then(
        (found) => !stop.signal.aborted && (setView(found), setError('')),
        (e: Error) => !stop.signal.aborted && (setView(null), setError(`没打开：${e.message}`)),
      )
    return () => stop.abort()
  }, [big, href, file.name, nonce])
  const shown: FileView | null = big ? { kind: 'other', why: big } : view
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Button size="sm" variant="ghost" onClick={onBack}>
          <ArrowLeft className="size-4" />
          返回
        </Button>
        <span className="min-w-0 flex-1 break-words font-medium">{file.name}</span>
        <a
          href={href}
          download={file.name}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label={`下载 ${file.name}`}
        >
          <Download className="size-4" />
        </a>
      </div>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {!shown && !error && <p className="text-muted-foreground">正在打开……</p>}
      {shown?.kind === 'other' && <p className="text-muted-foreground">{shown.why}</p>}
      {shown?.kind === 'text' &&
        (shown.text ? (
          <pre className="whitespace-pre-wrap break-words rounded-lg border p-3 text-xs leading-relaxed">{shown.text}</pre>
        ) : (
          <p className="text-muted-foreground">这个文件是空的。</p>
        ))}
      {shown?.kind === 'image' && (
        <img
          src={shown.src}
          alt={file.name}
          className="max-w-full rounded-lg border"
          onError={() => setView({ kind: 'other', why: '这张图打不开，下载下来看吧。' })}
        />
      )}
    </div>
  )
}

/** 联网: her container goes out only through nest's proxy, which refuses this machine and the local network
 * and logs every host. Off: no network at all. */
function Network({ base, online, onSwitch, busy }: {
  base: string
  online: boolean
  onSwitch: (on: boolean) => void
  busy: boolean
}) {
  const { subscribe } = useLive()
  const [open, setOpen] = useState(false)
  const [log, setLog] = useState<Connection[] | null>(null)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    if (!open) return
    let cancelled = false
    getJson<{ entries: Connection[] }>(`${base}/network`).then(
      (data) => !cancelled && (setLog(data.entries), setError('')),
      (e: Error) => !cancelled && setError(e.message),
    )
    return () => {
      cancelled = true
    }
  }, [base, open, nonce])
  useEffect(() => subscribe((item) => item.kind === 'workspace' && setNonce((n) => n + 1)), [subscribe])
  return (
    <div className="mt-4 space-y-2 rounded-lg border p-3">
      <label className="flex items-center gap-2">
        <Switch checked={online} disabled={busy} onCheckedChange={onSwitch} aria-label="她的容器联网" />
        <span className="font-medium">容器联网</span>
        <span className="text-xs text-muted-foreground">
          {online ? '能上公网（装包、下载），本机和局域网不通，访问过的网站都有记录' : '完全断网'}
        </span>
      </label>
      {online && (
        <p className="text-xs text-muted-foreground">
          联网时，她文件夹里的东西（包括你发给她的）理论上能被传出去；不想让她带出去的文件别放进来。
        </p>
      )}
      <button type="button" className="text-xs text-muted-foreground hover:text-foreground hover:underline" onClick={() => setOpen(!open)}>
        {open ? '收起联网记录' : '看联网记录'}
      </button>
      {open && error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      {open && log?.length === 0 && <p className="text-xs text-muted-foreground">还没有联网记录。</p>}
      {open && !!log?.length && (
        <ul className="max-h-64 space-y-0.5 overflow-y-auto font-mono text-[11px]">
          {log.map((c, i) => (
            <li key={i} className={c.ok ? '' : 'text-destructive'}>
              {formatTime(c.at)} {c.host}:{c.port} {c.ok ? '' : `✗ ${c.why}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
