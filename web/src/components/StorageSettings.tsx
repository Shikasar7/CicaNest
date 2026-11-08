import { useState } from 'react'
import { LoaderCircle } from 'lucide-react'

import { Segmented } from '@/components/Segmented'
import { Button } from '@/components/ui/button'
import { sendJson, type StorageView, type SystemSettings } from '@/lib/api'
import { formatBytes, formatTime } from '@/lib/format'
import { useLive } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'

const HISTORY = [7, 14, 30, 90]
const QQ_CACHE = [0, 3, 7, 30]

/** The saved value is always one of the choices, even one set some other way (NEST_MESSAGE_RETENTION_DAYS). */
function choices(values: number[], current: number, label: (n: number) => string) {
  const all = values.includes(current) ? values : [...values, current].sort((a, b) => a - b)
  return all.map((n) => ({ value: String(n), label: label(n) }))
}

function Size({ label, bytes }: { label: string; bytes: number }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm tabular-nums">{formatBytes(bytes)}</dd>
    </div>
  )
}

function lastRun(last: NonNullable<StorageView['last']>): string {
  const parts = []
  if (last.messages) parts.push(`${last.messages} 条消息`)
  if (last.records) parts.push(`${last.records} 条判断和监控记录`)
  if (last.files) parts.push(`${last.files} 个 QQ 文件`)
  if (last.stickers) parts.push(`${last.stickers} 张表情包`)
  const what = parts.length ? `删了${parts.join('、')}` : '没有要删的'
  const freed = last.freed >= 1024 * 1024 ? `，腾出 ${formatBytes(last.freed)}` : '' // a few KB: the WAL, not news
  return `上次清理 ${formatTime(last.at)}：${what}${freed}。`
}

/** 系统 page: how long nest keeps history, and (SnowLuma in Docker) QQ's pictures, videos and files. */
export function StorageSettings() {
  const { systemVersion } = useLive()
  const { data, error, missing, reload } = useLiveData<StorageView>('/api/system/storage', [systemVersion])
  const [busy, setBusy] = useState('')
  const [problem, setProblem] = useState('')

  if (missing) return <p className="text-sm text-muted-foreground">正在运行的 nest 还不支持这一项，重启 nest 后再看。</p>
  if (!data) return error ? <p role="alert" className="text-sm text-destructive">读取失败：{error}</p> : null

  const act = async (name: string, action: () => Promise<unknown>) => {
    setBusy(name)
    setProblem('')
    try {
      await action()
      reload()
    } catch (e) {
      setProblem((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  const save = (body: SystemSettings) => act('save', () => sendJson('PATCH', '/api/system/settings', body))
  const clean = () => {
    const what = data.qq_cache && data.qq_cache_days
      ? `删掉超过 ${data.history_days} 天的消息和记录，以及超过 ${data.qq_cache_days} 天的 QQ 图片、视频和文件？`
      : `删掉超过 ${data.history_days} 天的消息和记录？`
    if (!window.confirm(`${what}删了不能恢复。`)) return
    void act('clean', () => sendJson('POST', '/api/system/storage/clean'))
  }

  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <p id="history-days" className="text-sm font-medium">
          聊天记录保留
        </p>
        <Segmented
          labelledBy="history-days"
          disabled={busy !== ''}
          options={choices(HISTORY, data.history_days, (n) => `${n} 天`)}
          value={String(data.history_days)}
          onChange={(v) => void save({ history_days: Number(v) })}
        />
        <p className="text-xs text-muted-foreground">
          群消息、回复判断和监控记录超过这个天数就删掉，模型调用记录最多留 14 天。每天自动清理一次，改小以后下次清理时生效。
        </p>
      </div>
      {data.qq_cache && (
        <div className="space-y-1.5">
          <p id="qq-cache-days" className="text-sm font-medium">
            QQ 的图片、视频和文件保留
          </p>
          <Segmented
            labelledBy="qq-cache-days"
            disabled={busy !== ''}
            options={choices(QQ_CACHE, data.qq_cache_days, (n) => (n ? `${n} 天` : '不清理'))}
            value={String(data.qq_cache_days)}
            onChange={(v) => void save({ qq_cache_days: Number(v) })}
          />
          <p className="text-xs text-muted-foreground">
            只清理 Docker 容器里 bot 号收到的图片、视频和文件，QQ 的聊天记录本身不动。清掉的旧图片在 QQ 里会显示已过期。
          </p>
        </div>
      )}
      <dl className="grid grid-cols-2 gap-4 sm:max-w-xl sm:grid-cols-4">
        <Size label="nest 数据库" bytes={data.sizes.database} />
        <Size label="nest 日志" bytes={data.sizes.logs} />
        {data.sizes.qq_cache !== null && <Size label="QQ 图片和文件" bytes={data.sizes.qq_cache} />}
        {data.sizes.stickers !== undefined && (
          <Size label={`表情包 ${data.sticker_count ?? 0} 张`} bytes={data.sizes.stickers} />
        )}
      </dl>
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" disabled={busy !== ''} onClick={clean}>
          {busy === 'clean' && <LoaderCircle className="size-3.5 animate-spin" />}
          立即清理
        </Button>
        <span className="text-xs text-muted-foreground">
          {data.last ? lastRun(data.last) : 'nest 启动 1 分钟后清理第一次，之后每天一次。'}
        </span>
      </div>
      {problem && (
        <p role="alert" className="text-xs text-destructive">
          {problem}
        </p>
      )}
    </div>
  )
}

/** 一键备份 on the 系统 page: the file is made on the machine nest runs on, then downloaded from here. */
export function BackupButton() {
  const [busy, setBusy] = useState(false)
  const [made, setMade] = useState<{ name: string; size: number } | null>(null)
  const [problem, setProblem] = useState('')
  const backup = async () => {
    setBusy(true)
    setProblem('')
    try {
      setMade(await sendJson<{ name: string; size: number }>('POST', '/api/system/backups'))
    } catch (e) {
      setProblem((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-1.5">
      <p className="text-sm font-medium">备份</p>
      <p className="text-xs text-muted-foreground">
        打包数据库（聊天记录、Bot 设置、人设）和表情包，留最近 3 份。不含密钥和 QQ 登录数据。
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void backup()}>
          {busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}
          立即备份
        </Button>
        {made && (
          <a className="text-sm underline" href={`/api/system/backups/${made.name}`} download>
            下载 {made.name}（{formatBytes(made.size)}）
          </a>
        )}
      </div>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
    </div>
  )
}
