import type { Bot, PlatformView } from '@/lib/api'

export const QQ_LABEL = 'QQ 号'

const NAMES: Record<string, string> = { qq: 'QQ', discord: 'Discord', telegram: 'Telegram', slack: 'Slack', wecom: '企业微信' }

/** A platform's name, without asking the server. */
export const platformName = (key: string) => NAMES[key] ?? key

/** Whether a bot's platform can do this. A nest from before other platforms says nothing: QQ can do it all. */
export const can = (bot: Bot, capability: string) => !bot.capabilities || bot.capabilities.includes(capability)

export const platformOf = (bot: Bot, platforms: PlatformView[] | null) =>
  platforms?.find((p) => p.key === (bot.platform ?? 'qq')) ?? null

/** What to call a person's id on this bot's platform. */
export const userLabel = (bot: Bot, platforms: PlatformView[] | null) => platformOf(bot, platforms)?.user_label ?? QQ_LABEL

/** Why this is not a person's id on the bot's platform; '' when it may be. */
export function userProblem(user: string, bot: Bot, platforms: PlatformView[] | null) {
  const platform = platformOf(bot, platforms)
  if (!platform) return /^[1-9]\d{4,11}$/.test(user) ? '' : 'QQ 号要填 5–12 位数字'
  return new RegExp(`^(?:${platform.user_pattern})$`).test(user) ? '' : platform.user_error
}

/** A hint for fields that say who gets told something: QQ by number, anything else with its platform. */
export const RECIPIENT_HINT = '写成「平台:ID」，比如 discord:用户 ID、telegram:用户 ID、slack:成员 ID、wecom:userid；QQ 号也可以直接填'
