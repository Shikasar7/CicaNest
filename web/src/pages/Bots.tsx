import { AddBot } from '@/components/bots/AddBot'
import { BotCard } from '@/components/bots/BotCard'
import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { Bot } from '@/lib/api'
import { useBots, usePersonas, useProviders, useTones } from '@/lib/live'

/** A list the page can do without for a moment (the model and persona pickers fall back to /api/bots). */
function SideError({ what, error, loaded }: { what: string; error: string; loaded: boolean }) {
  if (!error) return null
  return (
    <p role="alert" className="text-sm text-destructive">
      读取{what}失败{loaded ? '，下面用的是上次的数据' : ''}：{error}
    </p>
  )
}

export function Bots() {
  const bots = useBots<Bot[]>('/api/bots')
  const providers = useProviders()
  const personas = usePersonas()
  const tones = useTones()
  const reload = () => {
    bots.reload()
    personas.reload()
  }
  if (!bots.data) return <FirstLoad error={bots.error} what=" bot " />
  if (bots.data.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <StaleNotice error={bots.error} />
        <Card>
          <CardHeader>
            <CardTitle>还没有 bot</CardTitle>
            <CardDescription>点「增加 bot」，选平台再填表：QQ 要扫码登录，Discord 和企业微信填 Token 就行；模型可以在「模型」页添加。</CardDescription>
          </CardHeader>
        </Card>
        <AddBot providers={providers.data} reload={reload} />
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <StaleNotice error={bots.error} />
      <SideError what="模型列表" error={providers.error} loaded={providers.data !== null} />
      <SideError what="人设列表" error={personas.error} loaded={personas.data !== null} />
      <div className="grid items-start gap-4 lg:grid-cols-2">
        {bots.data.map((bot) => (
          <BotCard key={bot.id} bot={bot} providers={providers.data} personas={personas.data} tones={tones.data} reload={reload} />
        ))}
      </div>
      <AddBot providers={providers.data} reload={reload} />
    </div>
  )
}
