import { useId, useRef, useState } from 'react'

import { AvatarCropper } from '@/components/bots/AvatarCropper'
import { groupName, useAction } from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { sendBlob, sendJson, type Bot, type Ok, type Persona, type PersonaSet, type PersonaSetSwitched } from '@/lib/api'
import { readPicture } from '@/lib/pictures'
import { cn } from '@/lib/utils'

const NAME_MAX = 20
// What QQ takes as an avatar (the backend refuses the rest).
const AVATAR_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']

/** Naming a new set, or renaming one. */
type Naming = { id: number | null; name: string }

/** A picture picked for a set's avatar, being cropped. */
type Cropping = { id: number; src: string }

/**
 * 人设方案: the persona this bot uses in each group and private chat, saved under a name together with who
 * she is (name, aliases, voice, model, and a picture for her account), so one account can switch between
 * characters in one click and switch back. Places added after a set was saved keep their persona.
 */
export function PersonaSets({ bot, personas, reload }: { bot: Bot; personas: Persona[] | null; reload: () => void }) {
  const sets = bot.persona_sets ?? []
  const base = `/api/bots/${encodeURIComponent(bot.id)}/persona-sets`
  const { busy, error, setError, run } = useAction(reload)
  const [naming, setNaming] = useState<Naming | null>(null)
  const [cropping, setCropping] = useState<Cropping | null>(null)
  const [done, setDone] = useState('')
  const [problems, setProblems] = useState<string[]>([])
  const ids = { name: useId(), error: useId() }
  const picker = useRef<HTMLInputElement>(null)
  const pickingFor = useRef<number | null>(null)
  const saved = sets.some((s) => s.current)
  const wears = (bot.capabilities ?? []).includes('set_profile')

  const personaName = (id: number | null) =>
    id === null ? '不用人设' : (personas?.find((p) => p.id === id)?.name ?? bot.channels.find((c) => c.persona_id === id)?.persona_name ?? '人设已不在列表里')
  const placeName = (channelId: number) => {
    const channel = bot.channels.find((c) => c.channel_id === channelId)
    return channel ? groupName(channel) : ''
  }

  const clear = () => {
    setError('')
    setDone('')
    setProblems([])
  }

  const startNaming = (next: Naming) => {
    setNaming(next)
    clear()
  }

  const submitName = async () => {
    if (!naming) return
    const name = naming.name.trim()
    if (!name) {
      setError('给方案起个名字')
      return
    }
    if ([...name].length > NAME_MAX) {
      setError(`方案名字最多 ${NAME_MAX} 个字`)
      return
    }
    const ok =
      naming.id === null
        ? await run(() => sendJson('POST', base, { name }))
        : await run(() => sendJson<Ok>('PATCH', `${base}/${naming.id}`, { name }))
    if (ok) {
      setNaming(null)
      setDone(naming.id === null ? `存好了：「${name}」` : '')
    }
  }

  const apply = async (set: PersonaSet) => {
    const who = set.identity
    const lines: string[] = []
    if (!saved) lines.push(`${bot.name}现在的样子还没存成方案，换成「${set.name}」后就要一个个手动改回来。`)
    if (who && wears) {
      lines.push(`会把 QQ 昵称改成「${who.name}」${who.avatar ? '，头像换成方案里的图' : ''}，群里有群名片的也改成这个名字。`)
    }
    if (lines.length && !window.confirm(`${lines.join('\n\n')}\n\n换成「${set.name}」？`)) return
    clear()
    let result: PersonaSetSwitched = { changed: 0, renamed: false, problems: [] }
    const ok = await run(async () => {
      result = await sendJson<PersonaSetSwitched>('POST', `${base}/${set.id}/apply`)
    })
    if (!ok) return
    const what = [result.renamed && who ? `现在是${who.name}` : '', result.changed ? `改了 ${result.changed} 个地方的人设` : '']
      .filter(Boolean)
      .join('，')
    setDone(what ? `已换成「${set.name}」：${what}` : `已经是「${set.name}」了`)
    setProblems(result.problems)
  }

  const resave = async (set: PersonaSet) => {
    if (!window.confirm(`用${bot.name}现在的名字、模型和每个地方的人设覆盖方案「${set.name}」？`)) return
    clear()
    if (await run(() => sendJson<Ok>('PATCH', `${base}/${set.id}`, { resave: true }))) setDone(`「${set.name}」已更新为现在的样子`)
  }

  const remove = async (set: PersonaSet) => {
    if (!window.confirm(`删掉方案「${set.name}」？里面用到的人设还留在人设库里。`)) return
    clear()
    await run(() => sendJson<Ok>('DELETE', `${base}/${set.id}`))
  }

  const pickAvatar = (set: PersonaSet) => {
    pickingFor.current = set.id
    clear()
    picker.current?.click()
  }

  const picked = async (file: File | undefined) => {
    const id = pickingFor.current
    if (!file || id === null) return
    if (!AVATAR_TYPES.includes(file.type)) {
      setError('只能用 PNG、JPEG、WebP 或 GIF 图片')
      return
    }
    try {
      setCropping({ id, src: await readPicture(file) })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const uploadAvatar = async (picture: Blob) => {
    if (!cropping) return
    if (await run(() => sendBlob<Ok>(`${base}/${cropping.id}/avatar`, picture))) {
      setCropping(null)
      setDone('头像存好了，换成这套时会换上')
    }
  }

  const removeAvatar = async (set: PersonaSet) => {
    if (!window.confirm(`去掉「${set.name}」的头像？以后换成这套时 QQ 头像保持不变。`)) return
    clear()
    await run(() => sendJson<Ok>('DELETE', `${base}/${set.id}/avatar`))
  }

  const nameForm = naming && (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        void submitName()
      }}
      onKeyDown={(e) => e.key === 'Escape' && setNaming(null)}
    >
      <div className="min-w-40 flex-1 space-y-1">
        <Label htmlFor={ids.name}>{naming.id === null ? '新方案的名字' : '改名为'}</Label>
        <Input
          id={ids.name}
          autoFocus
          value={naming.name}
          maxLength={NAME_MAX * 2}
          placeholder="比如：原来的、圣诞节"
          aria-invalid={Boolean(error)}
          aria-describedby={error ? ids.error : undefined}
          onChange={(e) => setNaming({ ...naming, name: e.target.value })}
        />
      </div>
      <Button type="submit" size="sm" disabled={busy}>
        {naming.id === null ? '保存' : '改名'}
      </Button>
      <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setNaming(null)}>
        取消
      </Button>
    </form>
  )

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        把{bot.name}现在的名字、别名、语音、模型和每个群、私聊用的人设存成一个方案，以后点一下就整套换过去，再点一下换回来
        {wears ? '；QQ 昵称和头像也跟着换' : ''}。记忆是共用的，不跟着换。
      </p>
      <input
        ref={picker}
        type="file"
        accept={AVATAR_TYPES.join(',')}
        className="hidden"
        onChange={(e) => {
          void picked(e.target.files?.[0])
          e.target.value = '' // the same file again still counts as a change
        }}
      />
      {sets.length > 0 && (
        <ul className="space-y-2">
          {sets.map((set) => {
            const who = set.identity
            // Clicking the set she wears again puts its nickname and picture on once more (QQ was offline, say).
            const again = set.current && who !== null && wears
            return (
              <li key={set.id} className={cn('space-y-2 rounded-lg border px-3 py-2.5', set.current && 'border-[var(--hue)]/50 bg-[var(--hue)]/5')}>
                <div className="flex flex-wrap items-center gap-2">
                  {who?.avatar && (
                    <img
                      src={`${base}/${set.id}/avatar?v=${who.updated_at}`}
                      alt=""
                      className="size-8 shrink-0 rounded-full border object-cover"
                    />
                  )}
                  <span className="font-medium break-all">{set.name}</span>
                  {set.current && <span className="rounded-full bg-[var(--hue)]/15 px-2 py-0.5 text-xs text-[var(--hue)]">正在用</span>}
                  <div className="ml-auto flex flex-wrap gap-1">
                    <Button
                      size="sm"
                      variant={set.current ? 'ghost' : 'outline'}
                      disabled={busy || (set.current && !again)}
                      title={again ? '再把这套的 QQ 昵称和头像换上一次' : undefined}
                      onClick={() => void apply(set)}
                    >
                      {again ? '重新换上' : '换成这套'}
                    </Button>
                    <Button size="sm" variant="ghost" disabled={busy} onClick={() => void resave(set)}>
                      用现在的覆盖
                    </Button>
                    <Button size="sm" variant="ghost" disabled={busy} onClick={() => startNaming({ id: set.id, name: set.name })}>
                      改名
                    </Button>
                    <Button size="sm" variant="ghost" className="text-destructive" disabled={busy} onClick={() => void remove(set)}>
                      删除
                    </Button>
                  </div>
                </div>
                {naming?.id === set.id && nameForm}
                {cropping?.id === set.id && (
                  <AvatarCropper
                    src={cropping.src}
                    busy={busy}
                    jpeg
                    onCancel={() => setCropping(null)}
                    onPickAnother={() => pickAvatar(set)}
                    onConfirm={(picture) => void uploadAvatar(picture)}
                    onError={(message) => {
                      setError(message)
                      setCropping(null)
                    }}
                  />
                )}
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
                  {who && (
                    <>
                      <dt className="text-muted-foreground">名字</dt>
                      <dd className="break-all">
                        {who.name}
                        {who.aliases.length > 0 && <span className="text-muted-foreground">（也叫 {who.aliases.join('、')}）</span>}
                      </dd>
                      <dt className="text-muted-foreground">模型</dt>
                      <dd className="break-all">{who.provider_id ?? '保持不变'}</dd>
                      {wears && (
                        <>
                          <dt className="text-muted-foreground">QQ 头像</dt>
                          <dd className="flex flex-wrap items-center gap-x-2">
                            <span>{who.avatar ? '换成方案里的图' : '保持不变'}</span>
                            <button type="button" className="text-[var(--hue)] hover:underline disabled:opacity-50" disabled={busy} onClick={() => pickAvatar(set)}>
                              {who.avatar ? '换一张' : '选一张'}
                            </button>
                            {who.avatar && (
                              <button type="button" className="text-muted-foreground hover:underline disabled:opacity-50" disabled={busy} onClick={() => void removeAvatar(set)}>
                                去掉
                              </button>
                            )}
                          </dd>
                        </>
                      )}
                    </>
                  )}
                  {set.items.length === 0 ? (
                    <dd className="col-span-2 text-muted-foreground">存的地方{bot.name}都已经不在了，换过去不会改任何人设</dd>
                  ) : (
                    set.items.map((item) => (
                      <div key={item.channel_id} className="contents">
                        <dt className="text-muted-foreground">{placeName(item.channel_id)}</dt>
                        <dd className="break-all">{personaName(item.persona_id)}</dd>
                      </div>
                    ))
                  )}
                </dl>
                {!who && <p className="text-xs text-muted-foreground">这是旧方案，只存了人设：点「用现在的覆盖」把名字和模型也存进来</p>}
              </li>
            )
          })}
        </ul>
      )}
      {naming?.id === null ? (
        nameForm
      ) : (
        <Button size="sm" variant="outline" disabled={busy || bot.channels.length === 0} onClick={() => startNaming({ id: null, name: '' })}>
          把现在的样子存成方案
        </Button>
      )}
      {bot.channels.length === 0 && <p className="text-xs text-muted-foreground">还没有群或私聊，没有人设可存</p>}
      {error && (
        <p id={ids.error} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {done && !error && (
        <p role="status" className="text-xs text-ok">
          {done}
        </p>
      )}
      {problems.length > 0 && (
        <ul role="alert" className="space-y-0.5 text-xs text-warn">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
