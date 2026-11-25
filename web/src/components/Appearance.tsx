import { useEffect, useId, useRef, useState } from 'react'
import { Check, Download, LoaderCircle, Monitor, Moon, Sun, Upload } from 'lucide-react'

import { Segmented, type SegmentedOption } from '@/components/Segmented'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ApiError, sendBlob, sendJson } from '@/lib/api'
import { useLive } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import { applyAppearance, setTheme, useDark, useTheme, type Appearance, type ThemeChoice, type ThemeEntry } from '@/lib/theme'
import { cn } from '@/lib/utils'

const MODES: SegmentedOption<ThemeChoice>[] = [
  { value: 'system', label: '跟随系统', Icon: Monitor },
  { value: 'light', label: '浅色', Icon: Sun },
  { value: 'dark', label: '深色', Icon: Moon },
]

function useAppearance() {
  const { systemVersion } = useLive()
  return useLiveData<Appearance>('/api/appearance', [systemVersion])
}

/** Keeps the console (or the chat window) in the colours picked in nest; mounted once in App. */
export function AppearanceSync() {
  const { data } = useAppearance()
  useEffect(() => {
    if (data) applyAppearance(data)
  }, [data])
  return null
}

/** A theme as a small picture of the page: its ground, a card with two lines of text, its button colour. */
function Swatch({ entry, chosen, dark, onPick }: { entry: ThemeEntry; chosen: boolean; dark: boolean; onPick: () => void }) {
  const c = dark ? entry.swatch.dark : entry.swatch.light
  return (
    <button
      type="button"
      role="radio"
      aria-checked={chosen}
      onClick={onPick}
      className={cn(
        'group flex flex-col gap-2 rounded-xl border p-2 text-left transition-colors hover:bg-accent',
        chosen && 'border-primary ring-2 ring-ring/40',
      )}
    >
      <span className="relative flex h-14 items-end gap-1.5 rounded-lg p-2" style={{ background: c.background }}>
        <span className="flex flex-1 flex-col gap-1 rounded-md p-1.5" style={{ background: c.card }}>
          <span className="h-1 w-3/4 rounded-full" style={{ background: c.text }} />
          <span className="h-1 w-1/2 rounded-full opacity-60" style={{ background: c.text }} />
        </span>
        <span className="h-4 w-7 rounded-md" style={{ background: c.accent }} />
        {chosen && (
          <span className="absolute top-1.5 right-1.5 grid size-4 place-items-center rounded-full bg-primary text-primary-foreground">
            <Check className="size-3" />
          </span>
        )}
      </span>
      <span className="px-0.5">
        <span className="block text-sm font-medium">{entry.name}</span>
        <span className="block truncate text-xs text-muted-foreground" title={entry.description}>
          {entry.plugin ? `插件 · ${entry.author}` : entry.description}
        </span>
      </span>
    </button>
  )
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

async function exportTheme(body: { theme: string; accent: string | null; name: string; author: string }): Promise<void> {
  const response = await fetch('/api/themes/export', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }).catch(() => {
    throw new ApiError(0, '连不上 nest 后台')
  })
  if (!response.ok) {
    const parsed = (await response.json().catch(() => null)) as { detail?: unknown } | null
    throw new ApiError(response.status, typeof parsed?.detail === 'string' ? parsed.detail : `导出失败（HTTP ${response.status}）`)
  }
  const name = /filename\*=UTF-8''([^;]+)/.exec(response.headers.get('content-disposition') ?? '')?.[1]
  download(await response.blob(), name ? decodeURIComponent(name) : 'theme.zip')
}

/** 系统 page → 外观: light or dark for this screen; the theme, an accent colour of your own, and sharing
 * themes (they are plugins: exported as a zip, put in from a zip or a .json, or found on a market). 设置 in
 * the chat shows it without the sharing. */
export function AppearanceSettings({ sharing = true }: { sharing?: boolean }) {
  const { data, error, missing, reload } = useAppearance()
  const mode = useTheme()
  const dark = useDark()
  const ids = { mode: useId(), themes: useId(), accent: useId(), name: useId(), author: useId(), file: useId() }
  const [busy, setBusy] = useState('')
  const [problem, setProblem] = useState('')
  const [note, setNote] = useState('')
  const [name, setName] = useState('')
  const [author, setAuthor] = useState('')
  const [accentDraft, setAccentDraft] = useState<string | null>(null)
  const file = useRef<HTMLInputElement>(null)

  const run = async (what: string, job: () => Promise<void>) => {
    setBusy(what)
    setProblem('')
    setNote('')
    try {
      await job()
    } catch (e) {
      setProblem((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  const change = (body: { theme?: string; accent?: string }) =>
    run('change', async () => {
      const view = await sendJson<Appearance>('PUT', '/api/appearance', body)
      applyAppearance(view)
      setAccentDraft(null)
      reload()
    })

  const putIn = (chosen: File | undefined) => {
    if (!chosen) return
    void run('import', async () => {
      const done = await sendBlob<{ plugin: string; themes: string[]; upgrade: string | null }>('/api/theme-packages', chosen)
      if (done.themes[0]) {
        const view = await sendJson<Appearance>('PUT', '/api/appearance', { theme: done.themes[0] })
        applyAppearance(view)
      }
      setNote(`${done.upgrade ? '更新' : '装'}好了，已经换上（插件 ${done.plugin}）`)
      reload()
    })
  }

  if (missing) {
    return <p className="text-sm text-muted-foreground">正在运行的 nest 还不支持换主题。更新并重启 nest 后再来。</p>
  }
  if (!data) {
    return error ? <p className="text-sm text-destructive">{error}</p> : <LoaderCircle className="size-4 animate-spin text-muted-foreground" />
  }

  const shown = dark ? data.colors.dark : data.colors.light
  const accentValue = accentDraft ?? data.accent ?? shown.accent
  const current = data.themes.find((t) => t.id === data.theme)
  const adjusted = data.accent && shown.accent !== data.accent

  return (
    <div className="flex flex-col gap-6">
      <div className="space-y-1.5">
        <p id={ids.mode} className="text-sm font-medium">
          浅色还是深色
        </p>
        <Segmented labelledBy={ids.mode} options={MODES} value={mode} onChange={setTheme} />
        <p className="text-xs text-muted-foreground">这个只记在这台设备上（每台电脑、每个浏览器各自选）。下面的主题和颜色记在 nest 里，所有地方都一样。</p>
      </div>

      <div className="space-y-2">
        <p id={ids.themes} className="text-sm font-medium">
          主题
        </p>
        {data.missing && (
          <p className="text-xs text-warn">原来选的主题所在的插件被关掉或卸载了，现在显示的是 nest 自己的样子。</p>
        )}
        <div role="radiogroup" aria-labelledby={ids.themes} className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-2">
          {data.themes.map((entry) => (
            <Swatch
              key={entry.id}
              entry={entry}
              dark={dark}
              chosen={!data.missing && entry.id === data.theme}
              onPick={() => void change({ theme: entry.id })}
            />
          ))}
        </div>
      </div>

      <div className="space-y-1.5">
        <label htmlFor={ids.accent} className="text-sm font-medium">
          强调色
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <input
            id={ids.accent}
            type="color"
            value={accentValue}
            onChange={(e) => setAccentDraft(e.target.value)}
            onBlur={() => accentDraft && accentDraft !== data.accent && void change({ accent: accentDraft })}
            className="h-8 w-12 cursor-pointer rounded-md border bg-transparent p-0.5"
          />
          {accentDraft && accentDraft !== data.accent && (
            <Button size="sm" onClick={() => void change({ accent: accentDraft })} disabled={busy !== ''}>
              用这个颜色
            </Button>
          )}
          {data.accent ? (
            <Button size="sm" variant="outline" onClick={() => void change({ accent: '' })} disabled={busy !== ''}>
              用主题自带的
            </Button>
          ) : (
            <span className="text-xs text-muted-foreground">现在用的是「{current?.name ?? '墨'}」自带的颜色</span>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          按钮、选中项和焦点框的颜色。太浅或太深时会自动调一点，保证按钮上的字和页面上的按钮都看得清
          {adjusted && `（现在这个外观里实际用的是 ${shown.accent}）`}。
        </p>
      </div>

      {sharing && (
        <div className="space-y-3 border-t pt-5">
          <p className="text-sm font-medium">分享和导入</p>
          <p className="text-xs text-muted-foreground">
            主题就是一种插件：导出的 .zip 能装到别的 nest 上，也能放进插件市场给别人下载；装上的主题在「插件」页可以关掉或卸载。
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <label htmlFor={ids.name} className="text-xs text-muted-foreground">
                主题名字
              </label>
              <Input id={ids.name} className="w-40" value={name} onChange={(e) => setName(e.target.value)} placeholder={current?.name ?? '我的主题'} maxLength={40} />
            </div>
            <div className="space-y-1">
              <label htmlFor={ids.author} className="text-xs text-muted-foreground">
                作者（可不填）
              </label>
              <Input id={ids.author} className="w-32" value={author} onChange={(e) => setAuthor(e.target.value)} maxLength={40} />
            </div>
            <Button
              variant="outline"
              disabled={busy !== ''}
              onClick={() =>
                void run('export', () =>
                  exportTheme({ theme: data.theme, accent: data.accent, name: name.trim() || current?.name || '我的主题', author }),
                )
              }
            >
              {busy === 'export' ? <LoaderCircle className="animate-spin" /> : <Download />}
              导出现在的配色
            </Button>
            <Button variant="outline" disabled={busy !== ''} onClick={() => file.current?.click()}>
              {busy === 'import' ? <LoaderCircle className="animate-spin" /> : <Upload />}
              导入主题（.zip 或 .json）
            </Button>
            <input
              ref={file}
              id={ids.file}
              type="file"
              accept=".zip,.json,application/zip,application/json"
              hidden
              onChange={(e) => {
                putIn(e.target.files?.[0])
                e.target.value = ''
              }}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            想从市场下载主题，去「插件」页的插件市场；要上传到市场，就把导出的 .zip 交给市场的维护者（或放进你自己的市场仓库）。
          </p>
        </div>
      )}

      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      {note && (
        <p role="status" className="text-sm text-ok">
          {note}
        </p>
      )}
    </div>
  )
}
