import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { ApiError, sendJson } from '@/lib/api'

/** 桌面聊天的图片和语音: pictures you send, her voice, 读出来, 语音输入. */

type Segments = { type: string; data: Record<string, unknown> }[]

export const mediaUrl = (name: string) => `/api/desk/media/${encodeURIComponent(name)}`

/** The files of the pictures sent in one message of the desk chat (their urls are `desk-media:<name>`). */
export function picturesOf(segments: Segments | undefined): string[] {
  return (segments ?? [])
    .map((s) => (s.type === 'image' ? String(s.data.url ?? '') : ''))
    .filter((url) => url.startsWith('desk-media:'))
    .map((url) => url.slice('desk-media:'.length))
}

/** The words sent with them. */
export const wordsOf = (segments: Segments | undefined) =>
  (segments ?? []).map((s) => (s.type === 'text' ? String(s.data.text ?? '') : '')).join('')

/** Her voice beside her words. */
export function voiceOf(segments: Segments | undefined): string | null {
  const found = (segments ?? []).find((s) => s.type === 'voice' && typeof s.data.media === 'string')
  return found ? String(found.data.media) : null
}

/** POST a file as the raw body; nest's errors read like every other request's. */
export async function postRaw<T>(path: string, blob: Blob): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { accept: 'application/json', 'content-type': blob.type || 'application/octet-stream' },
      body: blob,
    })
  } catch {
    throw new ApiError(0, '连不上 nest 后台')
  }
  const parsed = (await response.json().catch(() => undefined)) as { detail?: unknown } | undefined
  if (!response.ok) {
    const detail = typeof parsed?.detail === 'string' ? parsed.detail : `后台出错了（HTTP ${response.status}）`
    throw new ApiError(response.status, detail)
  }
  return parsed as T
}

// -- playing: one sound at a time, for the whole page --------------------------------------------------

let audio: HTMLAudioElement | null = null
let playing: string | null = null
let waiting: string[] = [] // 自动读出来: her next lines, after the one being read
const listeners = new Set<() => void>()
const changed = () => listeners.forEach((f) => f())

export function play(name: string): Promise<void> {
  stop()
  return start(name)
}

/** After what is playing (and what already waits), not instead of it. */
export function playNext(name: string): Promise<void> {
  if (playing === null) return start(name)
  waiting.push(name)
  return Promise.resolve()
}

function start(name: string): Promise<void> {
  audio = new Audio(mediaUrl(name))
  playing = name
  const done = () => {
    if (playing === name) {
      playing = null
      changed()
      const next = waiting.shift()
      if (next !== undefined) void start(next).catch(() => undefined)
    }
  }
  audio.onended = done
  audio.onerror = done
  changed()
  return audio.play().catch((e: unknown) => {
    done()
    throw e
  })
}

export function stop() {
  waiting = []
  if (audio) {
    audio.onended = null
    audio.onerror = null
    audio.pause()
    audio = null
  }
  if (playing !== null) {
    playing = null
    changed()
  }
}

/** The file playing now (null: nothing). */
export function usePlaying(): string | null {
  return useSyncExternalStore(
    (f) => {
      listeners.add(f)
      return () => listeners.delete(f)
    },
    () => playing,
  )
}

/** 读出来: her line in her voice (made once, then kept by nest). */
export async function readAloud(botId: string, messageId: number): Promise<string> {
  const made = await sendJson<{ media: string }>('POST', `/api/desk/${encodeURIComponent(botId)}/read-aloud`, {
    message_id: messageId,
  })
  return made.media
}

const AUTO_READ = 'nest.desk.autoRead'

/** 自动读出来: her new lines are read aloud as they come (this device only). */
export function useAutoRead(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem(AUTO_READ) === '1'
    } catch {
      return false
    }
  })
  const set = (value: boolean) => {
    setOn(value)
    try {
      localStorage.setItem(AUTO_READ, value ? '1' : '0')
    } catch {
      // private mode: for this page only
    }
    if (!value) stop()
  }
  return [on, set]
}

// -- 语音输入 ------------------------------------------------------------------------------------------

export const canRecord = (): boolean =>
  typeof window !== 'undefined' &&
  typeof MediaRecorder !== 'undefined' &&
  Boolean(navigator.mediaDevices?.getUserMedia)

const MAX_SECONDS = 60

/** Tap to start, tap to stop: the recording goes to the transcription service and its words come back. */
export function useRecorder(onWords: (text: string) => void) {
  const [state, setState] = useState<'idle' | 'recording' | 'busy'>('idle')
  const [seconds, setSeconds] = useState(0)
  const [error, setError] = useState('')
  const recorder = useRef<MediaRecorder | null>(null)
  const timer = useRef<number | null>(null)
  const words = useRef(onWords)
  useEffect(() => {
    words.current = onWords
  })

  const clear = () => {
    if (timer.current !== null) window.clearInterval(timer.current)
    timer.current = null
  }
  useEffect(
    () => () => {
      clear()
      const r = recorder.current
      if (r && r.state !== 'inactive') {
        r.ondataavailable = null
        r.onstop = null
        r.stop()
      }
      r?.stream.getTracks().forEach((t) => t.stop())
    },
    [],
  )

  const stopRecording = () => {
    const r = recorder.current
    if (r && r.state !== 'inactive') r.stop()
  }

  const start = async () => {
    setError('')
    if (!canRecord()) {
      setError('这里录不了音：手机要用 https 地址打开（比如 Tailscale 的 https 名字），电脑上要用桌面端或 localhost')
      return
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setError('没拿到麦克风：看看是不是没允许这个页面用麦克风')
      return
    }
    const chunks: Blob[] = []
    const r = new MediaRecorder(stream)
    recorder.current = r
    r.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data)
    r.onstop = async () => {
      clear()
      stream.getTracks().forEach((t) => t.stop())
      recorder.current = null
      const blob = new Blob(chunks, { type: r.mimeType || 'audio/webm' })
      if (blob.size < 1000) {
        setState('idle')
        return
      }
      setState('busy')
      try {
        const heard = await postRaw<{ text: string }>('/api/speech/listen', blob)
        if (heard.text) words.current(heard.text)
        else setError('没听清，再说一遍试试')
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setState('idle')
      }
    }
    r.start()
    setSeconds(0)
    setState('recording')
    const began = Date.now()
    timer.current = window.setInterval(() => {
      const s = Math.floor((Date.now() - began) / 1000)
      setSeconds(s)
      if (s >= MAX_SECONDS) stopRecording()
    }, 250)
  }

  return { state, seconds, error, clearError: () => setError(''), start, stop: stopRecording }
}
