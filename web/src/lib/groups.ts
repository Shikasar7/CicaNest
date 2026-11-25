/** 桌面群: you and several companions in one chat, in the desk only. */

export type GroupMember = { bot_id: string; name: string; channel_id: number; activity: string }
export type DeskGroup = {
  id: number
  name: string
  created_by: string
  members: GroupMember[]
  view: number | null
  /** 长期记忆 / 学黑话 of what is said there. Absent on an older nest. */
  remember?: boolean
  slang?: boolean
  last: { text: string; from_self: boolean; sender_name: string; sent_at: number; sticker: boolean } | null
}
export type Companion = { bot_id: string; name: string }

/** What the list shows under the group's name. */
export function groupLine(group: DeskGroup): string {
  const last = group.last
  if (!last) return group.members.map((m) => m.name).join('、')
  const said = last.sticker ? '[表情]' : last.text.replace(/\s+/g, ' ').trim() || '[消息]'
  return `${last.sender_name}：${said}`
}
