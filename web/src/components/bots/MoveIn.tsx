import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { NativeSelect } from '@/components/ui/native-select'
import { sendJson, type Bot } from '@/lib/api'
import { platformName } from '@/lib/platforms'

/** 接上平台账号: a companion who lives only on the desk moves into a bot with an account, with everything of hers
 * (nest.core.move_in). The account is added first (伙伴页: 接一个平台, or 从号开始加); which ones can take her, nest
 * says. */
export function MoveIn({ bot, bots, reload }: { bot: Bot; bots: Bot[]; reload: () => void }) {
  const rooms = bots.filter((b) => b.id !== bot.id && (b.platform ?? 'qq') !== 'desk')
  const [to, setTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const chosen = rooms.find((b) => b.id === to)
  const go = async () => {
    if (!chosen) return
    const where = `${platformName(chosen.platform ?? 'qq')} ${chosen.self_id}`
    const ask =
      `让${bot.name}搬进「${chosen.name}」（${where}）？\n\n` +
      `她的人设、聊天、记忆、回忆档案、任务、提醒、表情包和文件都跟着她，${chosen.name}这个名字和模型会换成她的。` +
      '搬过去就不能自动搬回来；搬之前会先留一份数据库备份。'
    if (!window.confirm(ask)) return
    setBusy(true)
    setProblem('')
    try {
      await sendJson('POST', `/api/bots/${encodeURIComponent(chosen.id)}/move-in`, { companion: bot.id })
      reload()
    } catch (e) {
      setProblem((e as Error).message)
      setBusy(false)
    }
  }
  return (
    <div className="space-y-2 text-sm">
      <p className="text-muted-foreground">
        已经有一个加好、还没人住的号：选它，{bot.name}就搬进去。
        搬过去以后群里还是什么都没开，要去哪个群、用什么群聊人设，到「群和私聊」里再设。
      </p>
      {rooms.length === 0 ? (
        <p>还没有有平台账号的 bot。</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect value={to} disabled={busy} aria-label="搬进哪个 bot" onChange={(e) => setTo(e.target.value)}>
            <option value="">选一个新加的 bot</option>
            {rooms.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}（{platformName(b.platform ?? 'qq')} {b.self_id}）
              </option>
            ))}
          </NativeSelect>
          <Button size="sm" variant="outline" disabled={!chosen || busy} onClick={() => void go()}>
            {busy ? '正在搬…' : '搬进去'}
          </Button>
        </div>
      )}
      {problem && (
        <p role="alert" className="text-destructive">
          没搬成：{problem}
        </p>
      )}
    </div>
  )
}
