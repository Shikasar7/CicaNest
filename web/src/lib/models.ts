import { useEffect, useState } from 'react'

import { getJson, type ProviderKind, type ProviderView } from '@/lib/api'

export const MODEL_KINDS: { kind: ProviderKind; label: string }[] = [
  { kind: 'openai_chat', label: 'OpenAI Chat 格式' },
  { kind: 'openai_responses', label: 'Responses 格式' },
  { kind: 'anthropic', label: 'Claude（Anthropic）' },
  { kind: 'codex', label: 'Codex 订阅' },
]

type PluginKind = { kind: string; label: string; options: string[]; plugin: string }
let pluginKinds: Promise<PluginKind[]> | null = null

/** Kinds of model that trusted plugins added: fetched once, then offered like nest's own.
 * Returns how many were added, so a page re-renders once they are known. */
export function usePluginKinds(): number {
  const [count, setCount] = useState(0)
  useEffect(() => {
    let cancelled = false
    pluginKinds ??= getJson<PluginKind[]>('/api/model-kinds').catch(() => [])
    void pluginKinds.then((kinds) => {
      for (const k of kinds) {
        const kind = k.kind as ProviderKind
        if (!MODEL_KINDS.some((m) => m.kind === kind)) MODEL_KINDS.push({ kind, label: `${k.label}（插件）` })
        KIND_OPTIONS[kind] = k.options
      }
      if (!cancelled) setCount(kinds.length)
    })
    return () => {
      cancelled = true
    }
  }, [])
  return count
}

export function kindLabel(kind: string): string {
  return MODEL_KINDS.find((k) => k.kind === kind)?.label ?? kind
}

export const CODEX_BASE_URL = 'https://chatgpt.com/backend-api/codex'
export const CODEX_AUTH_FILE = '~/.codex/auth.json'

/** Options each kind of model reads (mirrors the backend; anything else is rejected there). */
export const KIND_OPTIONS: Record<ProviderKind, string[]> = {
  openai_chat: ['proxy', 'timeout', 'temperature', 'extra_body', 'tools'],
  openai_responses: ['proxy', 'timeout', 'extra_body', 'input_format'],
  anthropic: ['proxy', 'timeout', 'extra_body', 'auth_header', 'effort', 'web_search'],
  codex: ['proxy', 'timeout', 'auth_file'],
}

export const OPTION_LABEL: Record<string, string> = {
  proxy: '代理',
  timeout: '超时',
  temperature: '温度',
  extra_body: '额外参数',
  input_format: '对话格式',
  auth_header: '密钥请求头',
  effort: '思考档',
  web_search: '联网搜索',
  tools: '工具调用',
  auth_file: '登录文件',
}

export const EFFORT_LABEL = { low: '低', medium: '中', high: '高' } as const

export function effortOf(value: unknown): keyof typeof EFFORT_LABEL {
  return value === 'medium' || value === 'high' ? value : 'low'
}

/** Request fields nest sets itself, so 额外参数 may not contain them. */
export const RESERVED_EXTRA_KEYS = ['model', 'messages', 'input', 'system', 'max_tokens', 'max_output_tokens', 'stream', 'temperature']

/** Stored options that a model of `kind` does not read (they are dropped on the next save). */
export function unusedOptions(options: Record<string, unknown>, kind: ProviderKind): string[] {
  return Object.keys(options).filter((key) => !KIND_OPTIONS[kind].includes(key))
}

export function urlHost(url: string): string | null {
  try {
    return new URL(url.trim()).host || null
  } catch {
    return null
  }
}

/** 127.0.0.0/8, localhost and ::1: traffic that never leaves this computer. */
export function isLoopbackHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '')
  return host === 'localhost' || host.endsWith('.localhost') || host === '::1' || /^127\.\d+\.\d+\.\d+$/.test(host)
}

/** A plain-http address to another machine: the key would travel unencrypted. */
export function isInsecureRemote(url: string): boolean {
  try {
    const parsed = new URL(url.trim())
    return parsed.protocol === 'http:' && !isLoopbackHost(parsed.hostname)
  } catch {
    return false
  }
}

/** Names of the live bots using this model: a change reaches what they say in the groups at once. */
export function liveUsers(provider: ProviderView): string[] {
  return provider.used_by.filter((u) => u.mode === 'live').map((u) => u.name)
}

/** The models a companion can talk with. */
export function usable(providers: ProviderView[] | null) {
  return (providers ?? []).filter((p) => p.health !== null)
}
