import { LayoutDashboard, X } from 'lucide-react'
import type { ReactNode } from 'react'

import { AppearanceSettings } from '@/components/Appearance'
import { MyAvatar } from '@/components/chat/MyAvatar'
import { DesktopOptions } from '@/components/DesktopOptions'
import { Switch } from '@/components/ui/switch'
import { useAutoRead } from '@/lib/desk-media'
import { chatView, desktop, openConsole } from '@/lib/desktop'

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-4 border-b py-6 first:pt-0 last:border-b-0 last:pb-0">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  )
}

const pill =
  'flex shrink-0 items-center gap-1.5 rounded-full border px-4 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground'

/** 设置 in the chat: your picture, how it looks, the sounds, and what the desktop app offers. The rest (bots,
 * models, plugins…) is in the console. */
export function ChatSettings({ onClose, onHome }: { onClose: () => void; onHome: () => void }) {
  const [autoRead, setAutoRead] = useAutoRead()
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b px-5 py-4">
        <h2 className="text-base font-semibold">设置</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="ml-auto grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-6">
        <div className="mx-auto max-w-2xl">
          <Section title="外观">
            <AppearanceSettings sharing={false} />
          </Section>
          <Section title="我的头像">
            <MyAvatar />
          </Section>
          <Section title="声音">
            <label className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <Switch checked={autoRead} onCheckedChange={setAutoRead} />
              自动读出来
              <span className="text-xs text-muted-foreground">她的新话用她的声音读出来，只记在这台设备上</span>
            </label>
          </Section>
          <Section title="她们什么时候来找你">
            <div className="flex flex-wrap items-center gap-3">
              <p className="min-w-0 flex-1 text-sm text-muted-foreground">
                一天几次、几点到几点安静，在小窝右上角的设置里改。
              </p>
              <button type="button" onClick={onHome} className={pill}>
                去小窝
              </button>
            </div>
          </Section>
          {desktop && (
            <Section title="桌面应用">
              <DesktopOptions />
            </Section>
          )}
          {chatView && (
            <Section title="更多">
              <div className="flex flex-wrap items-center gap-3">
                <p className="min-w-0 flex-1 text-sm text-muted-foreground">
                  模型、人设、平台账号、插件，以及主题的导入导出，都在控制台。
                </p>
                <button type="button" onClick={openConsole} className={pill}>
                  <LayoutDashboard className="size-4" />
                  打开控制台
                </button>
              </div>
            </Section>
          )}
        </div>
      </div>
    </div>
  )
}
