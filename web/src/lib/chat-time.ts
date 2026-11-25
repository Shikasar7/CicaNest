/** Time in the chat window: a line between messages only after a pause or on a new day. */

type Timed = { id: number; sent_at: number; from_self: boolean }

const PAUSE_S = 5 * 60 // a pause this long gets a time line between the messages
const WEEKDAYS = '日一二三四五六'
const pad = (n: number) => String(n).padStart(2, '0')

/** The line between messages: 「15:20」 today, 「昨天 15:20」, 「11月23日 星期日 15:20」. */
export function timeLine(seconds: number, now = new Date()): string {
  const date = new Date(seconds * 1000)
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const days = Math.round((today - day) / 86_400_000)
  if (days === 0) return clock
  if (days === 1) return `昨天 ${clock}`
  const year = date.getFullYear() === now.getFullYear() ? '' : `${date.getFullYear()}年`
  return `${year}${date.getMonth() + 1}月${date.getDate()}日 星期${WEEKDAYS[date.getDay()]} ${clock}`
}

export type Row<M extends Timed> =
  | { kind: 'time'; key: string; at: number }
  | { kind: 'message'; key: string; message: M; first: boolean }

/** Messages with a time line before each one after a pause or on a new day; `first`: the first of a run
 * by the same side (only it shows the avatar). */
export function rowsOf<M extends Timed>(messages: M[]): Row<M>[] {
  const rows: Row<M>[] = []
  let previous: M | null = null
  for (const message of messages) {
    const newDay =
      previous !== null &&
      new Date(previous.sent_at * 1000).toDateString() !== new Date(message.sent_at * 1000).toDateString()
    const paused = previous === null || newDay || message.sent_at - previous.sent_at >= PAUSE_S
    if (paused) rows.push({ kind: 'time', key: `t${message.id}`, at: message.sent_at })
    const first = paused || previous === null || previous.from_self !== message.from_self
    rows.push({ kind: 'message', key: `m${message.id}`, message, first })
    previous = message
  }
  return rows
}

/** The time beside a chat in the list: 「15:20」 today, 「昨天」, else 「11/23」. */
export function shortTime(seconds: number, now = new Date()): string {
  const date = new Date(seconds * 1000)
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const days = Math.round((today - day) / 86_400_000)
  if (days === 0) return `${pad(date.getHours())}:${pad(date.getMinutes())}`
  if (days === 1) return '昨天'
  return `${date.getMonth() + 1}/${date.getDate()}`
}
