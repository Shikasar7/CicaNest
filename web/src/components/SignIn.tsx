import { useEffect, useId, useState, type FormEvent } from 'react'
import { Eye, EyeOff, LoaderCircle } from 'lucide-react'

import { NestMark } from '@/components/NestMark'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { getJson, sendJson, type AuthInfo } from '@/lib/api'

/** In place of the console when this browser is not signed in: the password, or how to get in without one. */
export function SignIn() {
  const [info, setInfo] = useState<AuthInfo | null | 'old'>(null)
  const [password, setPassword] = useState('')
  const [shown, setShown] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0) // remounts the form so the shake replays on every wrong try
  const id = useId()

  useEffect(() => {
    // A nest from before passwords answers 404: only 启动.bat opens it.
    getJson<AuthInfo>('/api/auth').then(setInfo, () => setInfo('old'))
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!password || busy) return
    setBusy(true)
    setError('')
    try {
      await sendJson('POST', '/api/login', { password })
      window.location.reload() // a fresh start: the live stream and every page pick up the session
    } catch (e) {
      setError((e as Error).message)
      setAttempt((n) => n + 1)
      setBusy(false)
    }
  }

  const canUsePassword = info !== null && info !== 'old' && info.password_set
  const local = info !== null && info !== 'old' && info.local
  const start = (info !== null && info !== 'old' && info.start_hint) || '双击 启动.bat'

  return (
    <div className="grid min-h-svh place-items-center bg-surface px-4 py-10">
      <div className="page-enter w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <NestMark className="size-11" />
          <div>
            <h1 className="text-xl font-semibold tracking-tight">nest</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {canUsePassword ? '输入密码打开控制台' : '打开控制台'}
            </p>
          </div>
        </div>
        {info === null ? (
          <div className="grid h-40 place-items-center text-muted-foreground" role="status" aria-label="读取中">
            <LoaderCircle className="size-5 animate-spin" />
          </div>
        ) : canUsePassword ? (
          <form
            key={attempt}
            onSubmit={(e) => void submit(e)}
            className={`space-y-4 rounded-2xl border bg-card p-6 shadow-[var(--shadow-lift)] ${attempt ? 'nudge' : ''}`}
          >
            {/* For password managers: the "account" this password belongs to. */}
            <input type="text" name="username" autoComplete="username" value="nest" readOnly hidden />
            <div className="grid gap-1.5">
              <Label htmlFor={id}>密码</Label>
              <div className="relative">
                <Input
                  id={id}
                  name="password"
                  type={shown ? 'text' : 'password'}
                  autoComplete="current-password"
                  autoFocus
                  onFocus={(e) => e.target.select()} // after a wrong try, typing replaces it
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? `${id}-error` : undefined}
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShown((s) => !s)}
                  aria-label={shown ? '隐藏密码' : '显示密码'}
                  aria-pressed={shown}
                  className="absolute inset-y-0 right-0 grid w-10 place-items-center rounded-r-md text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:text-foreground"
                >
                  {shown ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
              {error && (
                <p id={`${id}-error`} role="alert" className="text-xs text-destructive">
                  {error}
                </p>
              )}
            </div>
            <Button type="submit" className="w-full" disabled={!password || busy}>
              {busy && <LoaderCircle className="size-4 animate-spin" />}
              登录
            </Button>
          </form>
        ) : (
          <div className="rounded-2xl border bg-card p-6 text-sm leading-relaxed shadow-[var(--shadow-lift)]">
            {local || info === 'old' ? (
              <p>请在这台电脑上{start}打开控制台。想从手机或别的电脑打开，可以在「系统」页的「远程访问」里设置密码。</p>
            ) : (
              <p>
                还没有设置登录密码。请在 nest 所在的电脑上打开控制台，到「系统」页的「远程访问」里设置；也可以在那台电脑上运行{' '}
                <code className="rounded bg-muted px-1 py-0.5 text-[13px]">nest set-password</code>。
              </p>
            )}
          </div>
        )}
        {canUsePassword && local && (
          <p className="mt-4 text-center text-xs text-muted-foreground">在这台电脑上也可以{start}直接打开</p>
        )}
      </div>
    </div>
  )
}
