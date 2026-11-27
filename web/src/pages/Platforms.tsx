import { useId, useState } from 'react'
import { Plug } from 'lucide-react'

import { AccountConnections } from '@/components/AccountConnections'
import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { QRLogin } from '@/components/QRLogin'
import { Segmented } from '@/components/Segmented'
import { PathField, Section, ServiceRow, useOnThisMachine, useRun, useSystem } from '@/components/system/parts'
import { StatusDot } from '@/components/StatusDot'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { sendJson, type QQContainer, type SystemSettings, type SystemView } from '@/lib/api'
import { useLive, usePlatforms } from '@/lib/live'
import { RECIPIENT_HINT } from '@/lib/platforms'
import { cn } from '@/lib/utils'

function QQLogin({ url, onThisMachine }: { url: string; onThisMachine: boolean }) {
  if (onThisMachine) {
    return (
      <Button size="sm" variant="outline" asChild>
        <a href={url} target="_blank" rel="noreferrer">
          扫码登录
        </a>
      </Button>
    )
  }
  const port = (() => {
    try {
      return new URL(url).port || '80'
    } catch {
      return '6081'
    }
  })()
  return (
    <p className="max-w-xs text-xs text-muted-foreground">
      扫码页只在 nest 那台电脑上能打开。在这台设备上：先运行{' '}
      <code className="rounded bg-muted px-1 py-0.5 text-[11px]">ssh -L {port}:127.0.0.1:{port} 那台电脑</code>，再打开 {url}
    </p>
  )
}

/** A NapCat account's 回退密码: typed here, only its MD5 kept on nest's machine. Saving makes the container again, so
 * the account drops for a few seconds. */
function PasswordForm({ box, who, onDone }: { box: QQContainer; who: string; onDone: () => void }) {
  const id = useId()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState<'' | 'save' | 'clear'>('')
  const [error, setError] = useState('')
  const send = async (value: string, kind: 'save' | 'clear') => {
    if (kind === 'clear' && !window.confirm(`清除 ${who || box.name} 的回退密码？容器会重建，这个号断开十几秒。`)) return
    setBusy(kind)
    setError('')
    try {
      await sendJson('PUT', '/api/system/qq/password', { container: box.name, password: value })
      setPassword('')
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  return (
    <form
      className="space-y-2 rounded-xl border bg-muted/40 p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (password) void send(password, 'save')
      }}
    >
      <Label htmlFor={id}>{who || box.self_id} 的 QQ 密码</Label>
      <div className="flex flex-wrap gap-2">
        <Input
          id={id}
          type="password"
          autoComplete="new-password"
          maxLength={64}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-64"
        />
        <Button type="submit" size="sm" disabled={!!busy || !password}>
          {busy === 'save' ? '正在重建容器…' : '保存'}
        </Button>
        {box.password_set && (
          <Button type="button" size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10 hover:text-destructive" disabled={!!busy} onClick={() => void send('', 'clear')}>
            {busy === 'clear' ? '正在重建容器…' : '清除'}
          </Button>
        )}
        <Button type="button" size="sm" variant="ghost" disabled={!!busy} onClick={onDone}>
          收起
        </Button>
      </div>
      <p className={cn('text-xs', error ? 'text-destructive' : 'text-muted-foreground')} role={error ? 'alert' : undefined}>
        {error ||
          `${box.password_set ? '已经设了回退密码，再保存会换成新的。' : ''}保存的登录失效时，QQ 先用这个密码登录，不行再出二维码。nest 那台电脑上只存密码的 MD5，不存密码本身，但拿到它也能登录这个号。账号被冻结、要人脸验证时它帮不上忙。保存或清除都会重建容器，这个号断开十几秒，登录状态不丢。`}
      </p>
    </form>
  )
}

/** One QQ container (docker mode): its state, start / restart / stop, its login QR code (NapCat, shown
 * here) or the page to scan it on, and its 回退密码 (NapCat). */
function ContainerRow({
  box,
  who,
  reload,
  onThisMachine,
}: {
  box: QQContainer
  who: string
  reload: () => void
  onThisMachine: boolean
}) {
  const run = useRun(reload)
  const [scanning, setScanning] = useState(false)
  const [password, setPassword] = useState(false)
  const passwordable = !!box.qr_login && !!box.self_id && box.password_set !== undefined
  const act = (action: 'start' | 'stop' | 'restart', question?: string) => {
    if (question && !window.confirm(question)) return
    void run.run(action, () => sendJson('POST', `/api/system/snowluma/${action}?container=${encodeURIComponent(box.name)}`))
  }
  // A stopped NapCat container (nest stops one that waits for a scan nobody makes): start it and show its code.
  const startAndScan = () => {
    setScanning(true)
    act('start')
  }
  const state = box.problem ? '连不上 Docker' : !box.found ? '容器还没建' : box.running ? '运行中' : '没有运行'
  const detail = box.problem ? (
    box.problem
  ) : !box.found ? (
    <>Docker 里还没有叫 {box.name} 的容器：在 nest 的 deploy/mac 文件夹里运行 docker compose up -d，见 deploy/mac/README.md。</>
  ) : box.qr_login ? (
    `把这个容器里的 QQ 接到 nest。号没上线时点「扫码登录」，用手机 QQ 扫码。登录失效后没人扫码时，nest 会停下这个容器，不让它一直刷新二维码。${box.password_set ? '已设回退密码。' : ''}`
  ) : (
    '把这个容器里的 QQ 接到 nest。号没上线时点「扫码登录」，用手机 QQ 扫码。'
  )
  return (
    <ServiceRow
      name={who ? `${box.name}（${who}）` : box.name}
      tone={box.problem ? 'bad' : !box.found ? 'off' : box.running ? 'ok' : 'bad'}
      state={state}
      detail={detail}
      error={run.error}
      below={
        password ? (
          <PasswordForm
            box={box}
            who={who}
            onDone={() => {
              setPassword(false)
              reload()
            }}
          />
        ) : scanning && box.running ? (
          <QRLogin container={box.name} onClose={() => setScanning(false)} />
        ) : undefined
      }
    >
      {box.found && !box.running && (
        <>
          {box.qr_login && (
            <Button size="sm" disabled={!!run.busy} onClick={startAndScan}>
              {run.busy === 'start' && scanning ? '启动中…' : '扫码登录'}
            </Button>
          )}
          <Button size="sm" variant={box.qr_login ? 'outline' : 'default'} disabled={!!run.busy} onClick={() => act('start')}>
            {run.busy === 'start' && !scanning ? '启动中…' : '启动'}
          </Button>
        </>
      )}
      {box.running && (
        <>
          {box.qr_login ? (
            !scanning && (
              <Button size="sm" variant="outline" onClick={() => setScanning(true)}>
                扫码登录
              </Button>
            )
          ) : (
            box.login_url && <QQLogin url={box.login_url} onThisMachine={onThisMachine} />
          )}
          <Button size="sm" variant="outline" disabled={!!run.busy} onClick={() => act('restart', `重启容器 ${box.name}？里面的 QQ 会重启，这个号要等它重新上线，一般不用重新扫码。`)}>
            {run.busy === 'restart' ? '重启中…' : '重启'}
          </Button>
          <Button size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10 hover:text-destructive" disabled={!!run.busy} onClick={() => act('stop', `停止容器 ${box.name}？这个号会断开，直到再次启动。登录状态会保留。`)}>
            {run.busy === 'stop' ? '停止中…' : '停止'}
          </Button>
        </>
      )}
      {passwordable && !password && (
        <Button size="sm" variant="outline" disabled={!!run.busy} onClick={() => setPassword(true)}>
          回退密码
        </Button>
      )}
    </ServiceRow>
  )
}


/** QQ's side of nest: SnowLuma or its containers, and the QQ accounts behind them. */
function QQServices({ system, reload }: { system: SystemView; reload: () => void }) {
  const snow = useRun(reload)
  const qq = useRun(reload)
  const onThisMachine = useOnThisMachine()
  const qqAccounts = system.accounts.filter((a) => a.id.startsWith('qq:'))
  const online = qqAccounts.filter((a) => a.state === 'connected').length
  const sl = system.snowluma
  const inDocker = sl.mode === 'docker'
  // Docker mode on a nest that lists its containers: a row each, with its own login page.
  const boxes = inDocker ? system.containers : undefined

  const stopSnow = () => {
    const what = inDocker
      ? `停止 SnowLuma 容器（${sl.container}）？里面的 QQ 也会一起停下，bot 全部断开，直到再次启动。登录状态会保留。`
      : '停止 SnowLuma？用它的 bot 都会断开，直到再次启动。QQ 本身不会退出。'
    if (!window.confirm(what)) return
    void snow.run('stop', () => sendJson('POST', '/api/system/snowluma/stop'))
  }
  const restartSnow = () => {
    const what = inDocker
      ? `重启 SnowLuma 容器（${sl.container}）？里面的 QQ 也会重启，bot 要等它们重新上线，一般不用重新扫码。`
      : '重启 SnowLuma？bot 会断开几秒钟，之后 nest 会自动重新连上。'
    if (!window.confirm(what)) return
    void snow.run('restart', () => sendJson('POST', '/api/system/snowluma/restart'))
  }
  const snowState = sl.problem
    ? '连不上 Docker'
    : !sl.configured
      ? inDocker
        ? '容器还没建'
        : '还没设置位置'
      : sl.running
        ? '运行中'
        : '没有运行'
  const snowDetail = sl.problem ? (
    sl.problem
  ) : !sl.configured ? (
    inDocker ? (
      <>
        Docker 里还没有叫 {sl.container} 的容器：在 nest 的 deploy/mac 文件夹里运行 docker compose up -d，见 deploy/mac/README.md。
      </>
    ) : (
      '在下面的「位置和网络」里填 SnowLuma 文件夹后才能从这里启动。'
    )
  ) : (
    <>
      负责把 QQ 接到 nest{inDocker ? `，在 Docker 容器 ${sl.container} 里运行` : ''}。bot 号没上线时，可以打开{' '}
      <a href={sl.webui_url} target="_blank" rel="noreferrer" className="underline underline-offset-4 hover:text-foreground">
        SnowLuma 管理页
      </a>{' '}
      查看{inDocker && !onThisMachine ? '（只在 nest 那台电脑上能打开）' : ''}。
    </>
  )
  return (
    <ul className="divide-y">
      {boxes?.map((box) => (
        <ContainerRow
          key={box.name}
          box={box}
          who={system.accounts.find((a) => box.self_id && a.self_id === box.self_id)?.name ?? ''}
          reload={reload}
          onThisMachine={onThisMachine}
        />
      ))}
      {!boxes && (
        <ServiceRow
          name="SnowLuma"
          tone={sl.problem ? 'bad' : !sl.configured ? 'off' : sl.running ? 'ok' : 'bad'}
          state={snowState}
          detail={snowDetail}
          error={snow.error}
        >
          {sl.configured && !sl.running && (
            <Button size="sm" disabled={!!snow.busy} onClick={() => void snow.run('start', () => sendJson('POST', '/api/system/snowluma/start'))}>
              {snow.busy === 'start' ? '启动中…' : '启动'}
            </Button>
          )}
          {sl.running && (
            <>
              <Button size="sm" variant="outline" disabled={!!snow.busy} onClick={restartSnow}>
                {snow.busy === 'restart' ? '重启中…' : '重启'}
              </Button>
              <Button size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10 hover:text-destructive" disabled={!!snow.busy} onClick={stopSnow}>
                {snow.busy === 'stop' ? '停止中…' : '停止'}
              </Button>
            </>
          )}
        </ServiceRow>
      )}
      <ServiceRow
        name="QQ"
        tone={qqAccounts.length === 0 ? 'off' : online === qqAccounts.length ? 'ok' : online === 0 ? 'bad' : 'wait'}
        state={`${online} / ${qqAccounts.length} 个号在线`}
        detail={
          <>
            {inDocker
              ? 'QQ 在 Docker 容器里运行。'
              : system.qq.clients
                ? `正在运行 ${system.qq.clients} 个 QQ 进程。`
                : '没有找到正在运行的 QQ。'}
            {online < qqAccounts.length &&
              (inDocker ? ' 没上线的号点「扫码登录」，用手机 QQ 扫码。' : ' 没上线的号需要在 QQ 里登录，SnowLuma 会自动接上。')}
            <span className="mt-1 flex flex-wrap gap-x-3">
              {qqAccounts.map((a) => (
                <span key={a.id} className="inline-flex items-center gap-1">
                  <StatusDot tone={a.state === 'connected' ? 'ok' : a.state === 'connecting' || a.state === 'reconnecting' ? 'wait' : 'bad'} className="size-1.5" />
                  {a.name || a.self_id}
                  {a.state === 'deaf' && <span className="text-destructive">收不到消息</span>}
                </span>
              ))}
            </span>
          </>
        }
        error={qq.error}
      >
        {inDocker && !boxes && system.qq.login_url && <QQLogin url={system.qq.login_url} onThisMachine={onThisMachine} />}
        {!inDocker && system.qq.configured && (
          <Button size="sm" variant="outline" disabled={!!qq.busy} onClick={() => void qq.run('open', () => sendJson('POST', '/api/system/qq/open'))}>
            {qq.busy ? '打开中…' : '打开 QQ'}
          </Button>
        )}
      </ServiceRow>
    </ul>
  )
}


/** Where QQ runs (SnowLuma as a program or in Docker) and where its pieces are. */
function QQPlaces({ system, save }: { system: SystemView; save: (body: SystemSettings) => Promise<unknown> }) {
  return (
    <>
      {system.snowluma.mode && (
        <div className="mb-5 space-y-1.5">
          <p id="snowluma-mode" className="text-sm font-medium">
            nest 管的 QQ 在哪里运行
          </p>
          <Segmented
            labelledBy="snowluma-mode"
            options={[
              { value: 'docker', label: 'Docker 容器（NapCat）' },
              { value: 'program', label: '这台电脑上的 SnowLuma' },
            ]}
            value={system.snowluma.mode}
            onChange={(mode) => void save({ snowluma_mode: mode })}
          />
          <p className="text-xs text-muted-foreground">
            {system.snowluma.mode === 'docker'
              ? '每个 QQ 号一个 NapCat 容器：nest 通过 docker 命令建、启动、停止它们，QQ 用扫码登录。'
              : 'SnowLuma 是这台电脑上的一个文件夹，QQ 是这台电脑上登录的 QQ。SnowLuma 已经不再公开发布，只适合已经装好的。'}
          </p>
        </div>
      )}
      <div className="grid gap-5 md:grid-cols-2">
        {system.snowluma.mode === 'docker' ? (
          <>
            <PathField
              label="QQ 容器"
              hint="docker compose 里 container_name 的值，几个容器用逗号隔开"
              placeholder="napcat-bot1, napcat-bot2, napcat-bot3"
              value={system.snowluma.container ?? 'snowluma'}
              onSave={(v) => save({ snowluma_container: v })}
            />
            <PathField
              label="QQ 容器的镜像"
              hint="新建 QQ 容器时用。NapCat 官方镜像 2026 年 11 月已经下架：这台电脑上没有的话，填一个你用 docker load 导入或从别处拿到的。留空用默认的 mlikiowa/napcat-docker:latest"
              placeholder="mlikiowa/napcat-docker:latest"
              value={system.qq.napcat_image_default ? '' : (system.qq.napcat_image ?? '')}
              onSave={(v) => save({ napcat_image: v })}
            />
            <PathField
              label="扫码页地址"
              hint="Docker 没说容器的扫码页在哪时用这个（SnowLuma 的远程桌面 noVNC）"
              placeholder="http://127.0.0.1:6081"
              value={system.qq.login_url ?? ''}
              onSave={(v) => save({ qq_login_url: v })}
            />
          </>
        ) : (
          <>
            <PathField
              label="SnowLuma 文件夹"
              hint="里面要有 node.exe 和 index.mjs"
              value={system.snowluma.dir}
              onSave={(v) => save({ snowluma_dir: v })}
            />
            <PathField
              label="QQ 程序"
              hint="QQ.exe 的完整路径，用来从这里打开 QQ"
              value={system.qq.executable}
              onSave={(v) => save({ qq_executable: v })}
            />
          </>
        )}
      </div>
    </>
  )
}

/** Nothing connected yet: what this page is for, and that a nest used only on the desk never needs it. */
function NoneYet() {
  const { data: platforms } = usePlatforms()
  const names = (platforms ?? []).filter((p) => p.key !== 'desk').map((p) => p.label)
  return (
    <section className="flex flex-col items-start gap-3 rounded-2xl border bg-card p-6">
      <span className="grid size-10 place-items-center rounded-full bg-muted text-muted-foreground">
        <Plug className="size-5" />
      </span>
      <h2 className="text-base font-semibold">还没有接任何平台</h2>
      <p className="max-w-prose text-sm text-muted-foreground">
        只在桌面上和伙伴聊天、做事、玩，用不到这一页。想让她也去{names.length > 0 ? names.join('、') : '别的聊天平台'}
        里说话时，到「伙伴」页给她接一个平台；接上以后，这里显示每个号的连接和在线情况。
      </p>
      <Button size="sm" variant="outline" asChild>
        <a href="#/bots">去伙伴页</a>
      </Button>
    </section>
  )
}

/** 平台接入: everything about QQ, Discord, 企业微信…: each account's connection, QQ's SnowLuma and containers,
 * who is told when one drops. A nest used only on the desk sees a short note and nothing to set. */
export function Platforms() {
  const { data, error, missing, reload } = useSystem()
  const { accounts } = useLive()
  const saveSettings = (body: SystemSettings) => sendJson('PATCH', '/api/system/settings', body).then(reload)
  if (missing) {
    return (
      <p className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
        正在运行的 nest 版本还不支持这一页。重启 nest 后再打开。
      </p>
    )
  }
  if (!data) return <FirstLoad error={error} what="平台接入" />
  const hasQQ = accounts.some((a) => a.platform === 'qq') || data.snowluma.configured || (data.containers?.length ?? 0) > 0
  return (
    <div className="flex flex-col gap-4">
      <StaleNotice error={error} />
      {accounts.length === 0 ? (
        <NoneYet />
      ) : (
        <Section title="账号">
          <AccountConnections />
        </Section>
      )}
      {hasQQ && (
        <Section title="QQ">
          <QQServices system={data} reload={reload} />
        </Section>
      )}
      {data.offline_alert && accounts.length > 0 && (
        <Section title="掉线提醒">
          <div className="grid gap-5 md:grid-cols-2">
            <PathField
              label="私聊通知谁"
              hint={`某个号掉线够久时，由同平台还在线的号私聊这个人一次，重新连上时再说一声。留空表示不提醒。${RECIPIENT_HINT}`}
              placeholder="你自己在平台上的号"
              value={data.offline_alert.to}
              validate={(v) =>
                v && !v.includes(':') && !/^[1-9]\d{4,11}$/.test(v)
                  ? '不带平台前缀时要填 QQ 号（5–12 位数字），别的平台写成「平台:ID」。留空表示不提醒'
                  : undefined
              }
              onSave={(v) => saveSettings({ offline_alert_to: v })}
            />
            <PathField
              label="掉线多少分钟后通知"
              hint="1–120 分钟。短暂掉线（自动重启、平台自己重连）不会提醒"
              value={String(data.offline_alert.minutes)}
              validate={(v) => (/^\d+$/.test(v) && +v >= 1 && +v <= 120 ? undefined : '填 1 到 120 之间的整数')}
              onSave={(v) => saveSettings({ offline_alert_minutes: Number(v) })}
            />
          </div>
        </Section>
      )}
      {hasQQ ? (
        <Section title="QQ 的运行方式">
          <QQPlaces system={data} save={saveSettings} />
        </Section>
      ) : (
        <details className="rounded-2xl border bg-card px-5 py-4">
          <summary className="cursor-pointer text-sm font-semibold">要接 QQ 的话：QQ 的运行方式</summary>
          <p className="mt-2 mb-4 text-xs text-muted-foreground">
            QQ 要靠一个 OneBot 11 协议端（NapCat、SnowLuma 这类）接到 nest。想让 nest 替每个号建 NapCat 容器，先在这里说好它在哪里运行、用哪个镜像；在「伙伴」页加号时选「接我自己运行的协议端」的话，用不到这一节。
          </p>
          <QQPlaces system={data} save={saveSettings} />
        </details>
      )}
    </div>
  )
}
