export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** GET a JSON endpoint of the local backend. Auth rides on the same-origin session cookie. */
export function getJson<T>(path: string): Promise<T> {
  return request<T>('GET', path)
}

/** Write to the backend. An empty (or 204) response body resolves to undefined. */
export function sendJson<T>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown): Promise<T> {
  return request<T>(method, path, body)
}

/** POST JSON and get audio back (试听): errors read like every other request's. */
export async function fetchAudio(path: string, body: unknown): Promise<Blob> {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }).catch(() => {
    throw new ApiError(0, '连不上 nest 后台')
  })
  if (response.ok) return response.blob()
  const parsed = (await response.json().catch(() => null)) as { detail?: unknown } | null
  throw new ApiError(response.status, typeof parsed?.detail === 'string' ? parsed.detail : serverError(response.status))
}

/** PUT a file (e.g. a picture) as the raw request body. */
export function sendBlob<T>(path: string, blob: Blob): Promise<T> {
  return request<T>('PUT', path, blob)
}

const serverError = (status: number) => `后台出错了（HTTP ${status}），详情见 data/logs/nest.log`

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response
  let text: string
  try {
    response = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: {
        accept: 'application/json',
        ...(body === undefined
          ? {}
          : { 'content-type': body instanceof Blob ? body.type || 'application/octet-stream' : 'application/json' }),
      },
      body: body === undefined ? undefined : body instanceof Blob ? body : JSON.stringify(body),
    })
    text = await response.text()
  } catch {
    throw new ApiError(0, '连不上 nest 后台')
  }
  let parsed: unknown
  try {
    parsed = text ? JSON.parse(text) : undefined
  } catch {
    throw new ApiError(response.status, serverError(response.status))
  }
  if (!response.ok) {
    // nest's own errors carry one Chinese sentence in `detail`; anything else is not meant for the user.
    const detail = (parsed as { detail?: unknown } | undefined)?.detail
    if (response.status >= 500) {
      const message = serverError(response.status)
      throw new ApiError(response.status, typeof detail === 'string' ? `${detail}。${message}` : message)
    }
    if (parsed === undefined) throw new ApiError(response.status, serverError(response.status))
    throw new ApiError(response.status, typeof detail === 'string' ? detail : '填写的内容不对')
  }
  return parsed as T
}

/** What every DELETE (and the bot/channel PATCH) answers. */
export type Ok = { ok: true }

export type Status = {
  version: string
  started_at: number
  uptime_seconds: number
  schema_version: number
}

/** GET /api/system: the processes nest depends on. */
export type QQContainer = {
  name: string
  /** The container exists. */
  found: boolean
  running: boolean
  /** Docker's word for it (running, exited…). */
  status: string
  /** Why Docker could not be asked. */
  problem: string
  login_url: string
  /** NapCat: the console can show its login QR code (GET /api/system/qq/login). Absent on an older nest. */
  qr_login?: boolean
  /** The QQ number it logs in (its ACCOUNT), "" when it does not say. */
  self_id?: string
  /** NapCat has a 回退密码 (PUT /api/system/qq/password). Absent on an older nest. */
  password_set?: boolean
}

/** GET /api/system/qq/login?container=…: what to show for scanning a NapCat container's login QR code. */
export type QQLoginView = {
  container: string
  self_id: string
  /** The bot account's name in nest, "" when unknown. */
  name: string
  /** The account is connected: nothing to scan. */
  logged_in: boolean
  /** The container runs (nest stops one that waits for a scan nobody makes). Absent on an older nest. */
  running?: boolean
  /** A data: URL of the PNG while QQ waits for a scan, else null. */
  qr: string | null
  detail: string
}

export type SystemView = {
  nest: { version: string; pid: number; started_at: number; uptime_seconds: number; memory_mb: number }
  snowluma: {
    /** "program": a folder nest starts itself (Windows); "docker": a container (Docker). Absent on
     * a nest from before Docker mode, which only knows programs. */
    mode?: 'program' | 'docker'
    /** The QQ containers' names, comma-separated (docker mode: one NapCat per account, or SnowLuma). */
    container?: string
    /** Docker's word for the container (running, exited…), in docker mode. */
    status?: string
    /** Why Docker could not be asked (not installed, not running), in docker mode. */
    problem?: string
    dir: string
    /** program: the folder has SnowLuma; docker: the container exists. */
    configured: boolean
    running: boolean
    pid: number | null
    webui_url: string
  }
  /** clients: QQ programs running here; null in docker mode (they are inside the container).
   * login_url: the container's remote desktop page for scanning the login QR code (docker mode). */
  qq: { executable: string; configured: boolean; clients: number | null; login_url?: string }
  /** Docker mode: each QQ container, with the page to scan its login QR code on. Empty in program mode;
   * absent on a nest from before several containers. */
  containers?: QQContainer[]
  accounts: { id: string; self_id: string; name: string; state: AdapterState; detail: string }[]
  /** The proxy 监控 fetch through when asked to: "" = none. Absent on a nest from before 监控. */
  fetch_proxy?: string
  /** 掉线提醒: the QQ number told when a bot is down that long ("" = off). Absent on an older nest. */
  offline_alert?: { to: string; minutes: number }
}

/** PATCH /api/system/settings. `fetch_proxy`: "" or http(s)://host:port. */
export type SystemSettings = {
  snowluma_mode?: 'program' | 'docker'
  snowluma_dir?: string
  qq_executable?: string
  snowluma_container?: string
  qq_login_url?: string
  fetch_proxy?: string
  /** Days of history kept (1–365). */
  history_days?: number
  /** Days QQ's pictures, videos and files are kept in the Docker container; 0 = never cleaned. */
  qq_cache_days?: number
  offline_alert_to?: string
  /** 1–120. */
  offline_alert_minutes?: number
}

/** GET /api/system/storage (and POST /api/system/storage/clean): the 「存储」 section. Sizes in bytes. */
export type StorageView = {
  history_days: number
  qq_cache_days: number
  /** SnowLuma runs in Docker: the container's QQ cache is nest's to clean. */
  qq_cache: boolean
  /** `stickers` is absent on a nest from before 表情包. */
  sizes: { database: number; logs: number; qq_cache: number | null; stickers?: number }
  sticker_count?: number
  /** The last cleanup since nest started; null before the first (a minute after start, then daily). */
  last: { at: number; messages: number; records: number; files: number; freed: number; stickers?: number } | null
}

/** GET /api/platforms: a platform nest can run, and the form to add one of its bots. */
export type PlatformField = {
  name: string
  label: string
  /** Goes to .env; only whether it is set ever comes back. */
  secret: boolean
  required: boolean
  default: string
  hint: string
  /** It is the account's id: fixed once the account exists. */
  identity: boolean
}

export type PlatformView = {
  key: string
  label: string
  capabilities: string[]
  fields: PlatformField[]
  /** What a person's id is called and looks like there (a private chat, who gets told things). */
  user_label: string
  user_pattern: string
  user_error: string
  /** A test connects as the bot and would throw it offline: only offered while adding one. */
  probe_kicks: boolean
}

/** POST /api/accounts/test and, for a platform other than QQ, POST /api/accounts/{id}/connection/test. */
export type ConnectionTrial = { ok: boolean; self_id: string; name: string; detail: string }

/** GET /api/accounts/connections: how each account is reached. Secrets are never sent, only whether set. */
export type QQConnection = {
  id: string
  /** Absent on a nest from before other platforms. */
  platform?: string
  /** A platform other than QQ: what its form holds (no secrets), and which secrets are set. */
  fields?: Record<string, string>
  secrets?: Record<string, boolean>
  self_id: string
  name: string
  http_url: string
  ws_url: string
  http_token: boolean
  ws_token: boolean
  state: AdapterState
  detail: string
}

/** Addresses: absent keeps the saved one. Tokens: absent keeps the saved one, "" removes it. */
export type QQConnectionChange = { http_url?: string; ws_url?: string; http_token?: string; ws_token?: string }

/** POST /api/accounts/{id}/connection/test. */
export type ConnectionCheck = { http: { ok: boolean; detail: string }; ws: { ok: boolean; detail: string } }

/** One line of nest's log (GET /api/logs, SSE "log"). */
export type LogLine = { id: number; at: number; level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'; logger: string; message: string }

export type AdapterState =
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'auth_failed'
  | 'misconfigured'
  | 'offline'
  | 'deaf'
  | 'stopped'

export type AccountStatus = { state: AdapterState; detail: string; since: number }

export type Account = {
  id: string
  platform: string
  self_id: string
  name: string
  enabled: boolean
  status: AccountStatus
}

export type Channel = {
  id: number
  account_id: string
  account_name: string
  kind: 'group' | 'private'
  external_id: string
  name: string
  last_message_at: number | null
  message_count: number
}

export type Message = {
  id: number
  channel_id: number
  external_id: string
  sender_id: string
  sender_name: string
  from_self: boolean
  text: string
  segments: { type: string; data: Record<string, unknown> }[]
  mentions: string[]
  reply_to: string | null
  sent_at: number
  received_at: number
  source: 'live' | 'backfill'
  recalled_at: number | null
}

export type Activity = 'mentions' | 'occasional' | 'active'
export type BotMode = 'observe' | 'live' | 'paused'

export type ProviderHealth = {
  id: string
  kind: string
  model: string
  available: boolean
  retry_in: number
  consecutive_failures: number
  last_error: string
  last_ok_at: number | null
}

export type SpeechServiceId = 'fish' | 'minimax' | 'openai'

export type SpeechService = {
  id: SpeechServiceId
  label: string
  key_set: boolean
  key_from_env: boolean
  ready: boolean
  /** The service's system voices, when known for the chosen model. */
  known: SpeechVoice[]
}

export type SpeechVoice = { id: string; name: string }

export type SpeechSettings = {
  fish_model: string
  minimax_model: string
  minimax_region: 'cn' | 'intl'
  openai_url: string
  openai_model: string
  proxy: string
  hourly_per_bot: number
  group_gap: number
  max_chars: number
  /** 音色库: the voices written down for each service. */
  library: Partial<Record<SpeechServiceId, SpeechVoice[]>>
}

/** GET /api/speech. */
export type SpeechView = {
  settings: SpeechSettings
  services: SpeechService[]
  fish_models: string[]
  minimax_models: string[]
  /** 欢迎新人, per group (only platforms that report joins); absent on an older nest. */
  groups?: { key: string; name: string; welcome: boolean }[]
}

export type BotChannel = {
  channel_id: number
  /** "private": a private chat with one person (group_id is their id there); absent on an older nest. */
  kind?: 'group' | 'private'
  group_id: string
  name: string
  enabled: boolean
  activity: Activity
  persona_name: string
  persona: string
  /** The persona assigned in this group; null = none (the default 你是群里的{name}。). */
  persona_id: number | null
  cooldown_seconds: number
  /** 发表情; absent on a nest from before 表情包. */
  sticker_mode?: StickerMode
  voice_mode?: StickerMode
  /** 语音频率: percent of the replies the model did not mark that are spoken anyway. */
  voice_rate?: number
  /** 一次最多发: messages one reply may come as, a sticker included. */
  reply_lines?: number
  /** 分条频率: percent of the replies that may come as several messages. */
  split_rate?: number
  /** 用黑话; absent on a nest from before 黑话. */
  use_slang?: boolean
  /** 答题: called with a real question, the bot answers it properly (searching the web first when its
   * model can); absent on a nest from before 答题. */
  answer_questions?: boolean
  /** 语气 keys (GET /api/tones): for 答题, and for everyday chat; absent on a nest from before them. */
  answer_tone?: string
  chat_tone?: string
  /** 问题记录官 (groups only, one bot per group); absent on a nest from before 问题记录. */
  recorder?: boolean
}

/** A 语气 (GET /api/tones): built-in ones can be rewritten and reset, added ones deleted when unused. */
export type Tone = {
  key: string
  label: string
  manner: string
  builtin: boolean
  /** A built-in one that differs from how it shipped. */
  edited: boolean
  used_by: { bot: string; channel: string; as: 'answer' | 'chat' }[]
}

/** Whether a bot sends 表情包 in a group: "trial" records what it would have sent, never sends it. */
export type StickerMode = 'off' | 'trial' | 'on'

export type Outcome = 'would_reply' | 'replied' | 'skipped' | 'suppressed' | 'failed'

export type Bot = {
  id: string
  /** 发语音: "<service>:<voice id>"; absent on a nest from before it. */
  voice?: string
  name: string
  self_id: string
  /** "qq", "discord", "wecom"; absent on a nest from before other platforms, which has only QQ. */
  platform?: string
  /** What its platform can do (GET /api/platforms); absent like `platform`: everything. */
  capabilities?: string[]
  aliases: string[]
  mode: BotMode
  /** The configured model; set while `provider` is null means that model failed to load. */
  provider_id: string | null
  provider: ProviderHealth | null
  /** 备用模型: speaks when `provider_id` is out of quota or down. */
  backup_provider_id?: string | null
  today: Partial<Record<Outcome, number>>
  /** Last time another program spoke as this bot's account (unix seconds), if seen. */
  foreign_at: number | null
  /** The bot's latest line, sent or would-have-been, however long ago; absent on an older nest. */
  last_line?: Decision | null
  channels: BotChannel[]
}

/** Where a bot's console picture comes from: its QQ avatar or one the user uploaded. */
export type AvatarSource = 'qq' | 'custom'

/** GET /api/avatars entry: the picture shown (null: the brush character) and when each stored one changed
 * (unix seconds; null: none stored). */
export type AvatarView = { chosen: AvatarSource | null; qq: number | null; custom: number | null }

export const avatarPath = (botId: string) => `/api/bots/${encodeURIComponent(botId)}/avatar`

/** A stored picture's address. Pictures are cached for good, so a new one must be a new URL: the version. */
export const avatarUrl = (botId: string, source: AvatarSource, version: number) =>
  `${avatarPath(botId)}/${source}?v=${version}`

export type Decision = {
  id: number
  bot_id: string
  bot_name: string
  channel_id: number
  trigger_id: number | null
  trigger: 'mention' | 'chime_in' | 'source' | 'welcome'
  outcome: Outcome
  reason: string
  reply: string | null
  quote: boolean
  regenerated: boolean
  created_at: number
  trigger_sender: string | null
  trigger_text: string | null
  /** The 表情包 the reply carried (absent on older decisions). */
  sticker_id?: number | null
  /** "sent" | "trial" (would have sent) | "failed" | "unknown"; null: no sticker went out. */
  sticker_state?: 'sent' | 'trial' | 'failed' | 'unknown' | null
  /** 黑话 explained in the prompt. */
  slang_terms?: string[]
}

export type Group = {
  key: string
  kind: 'group' | 'private'
  external_id: string
  name: string
  last_message_at: number | null
  channels: { channel_id: number; account_id: string; account_name: string; message_count: number }[]
}

export type Timeline = {
  viewer: string
  viewer_channel_id: number
  channel_ids: number[]
  messages: Message[]
  decisions: Decision[]
}

/** PATCH /api/bots/{id}: send only what changed (at least one field). Answers {ok: true}. */
export type BotPatch = {
  mode?: BotMode
  name?: string
  aliases?: string[]
  provider_id?: string | null
  backup_provider_id?: string | null
}

/** PATCH /api/bots/{id}/channels/{channel_id}. Answers {ok: true}. */
export type ChannelPatch = {
  enabled?: boolean
  activity?: Activity
  /** Integer seconds, 0–600. */
  cooldown_seconds?: number
  /** null = no persona. */
  persona_id?: number | null
  sticker_mode?: StickerMode
  voice_mode?: StickerMode
  /** 语音频率: percent of the replies the model did not mark that are spoken anyway. */
  voice_rate?: number
  reply_lines?: number
  split_rate?: number
  use_slang?: boolean
  answer_questions?: boolean
  answer_tone?: string
  chat_tone?: string
  recorder?: boolean
}

// ---- 问题记录 (/api/issues) ----

export type IssueKind = 'unsolved' | 'wrong' | 'bug' | 'request' | 'feedback'
export type IssueStatus = 'open' | 'done' | 'ignored'

/** What a group's 记录官 wrote down: who said it, when, and what the bots answered. */
export type Issue = {
  id: number
  group_id: string
  group_name: string
  bot_id: string
  bot_name: string
  message_id: number | null
  said_at: number
  speaker: string
  speaker_id: string
  kind: IssueKind
  summary: string
  quote: string
  ai_reply: string
  status: IssueStatus
  created_at: number
  reported_at: number | null
}

export type IssuesView = {
  issues: Issue[]
  groups: { group_id: string; name: string }[]
  counts: Record<IssueStatus, number>
  recorders: { bot_id: string; bot_name: string; group_id: string; group_name: string }[]
  /** The daily digest: the QQ number it goes to ("" = off) and the times of day ("HH:MM"). */
  digest: { to: string; times: string[] }
}

export type IssuesRun = { ok: boolean; detail: string }

export type StickerStatus = 'pending' | 'ready' | 'unsuitable' | 'failed'

/** One collected 表情包. */
export type Sticker = {
  id: number
  sha256: string
  kind: 'image' | 'mface' | 'upload' | 'drawn'
  content_type: string
  bytes: number
  width: number
  height: number
  animated: boolean
  group_key: string
  seen_count: number
  first_seen_at: number
  last_seen_at: number
  status: StickerStatus
  status_at: number
  /** Why it is unsuitable or failed. */
  note: string
  description: string
  /** The words in the picture. */
  caption: string
  tags: string[]
  /** The description was changed by hand. */
  edited: boolean
  /** 「保留」: never removed to make room. */
  kept: boolean
  /** Only this bot sends it (a drawn sticker of its character); null: any bot. */
  bot_id: string | null
  used_count: number
  last_used_at: number | null
}

export const stickerUrl = (sticker: Pick<Sticker, 'id' | 'sha256'>) =>
  `/api/stickers/${sticker.id}/image?v=${sticker.sha256.slice(0, 12)}`

export type StickerSettings = {
  vision_provider: string | null
  max_count: number
  max_mb: number
  describe_per_hour: number
  /** 图片说明: pictures in the chat the vision model may look at per hour; 0 turns it off. */
  notes_per_hour: number
  hourly_per_bot: number
  group_gap: number
}

/** GET /api/stickers: a page of the collection, and everything the page's settings show. */
export type StickerList = {
  items: Sticker[]
  total: number
  settings: StickerSettings
  /** The chosen vision model loaded (false: none chosen, or it is gone / broken). */
  vision_loaded: boolean
  /** A loaded model can draw (a Codex one). */
  can_draw: boolean
  count: number
  bytes: number
  counts: Partial<Record<StickerStatus, number>>
  groups: { key: string; name: string; collect: boolean }[]
}

export type SlangEntry = {
  id: number
  group_key: string
  term: string
  meaning: string
  example: string
  /** "auto": learned; "edited": changed or confirmed by hand; "manual": added by hand. */
  source: 'auto' | 'edited' | 'manual'
  hits: number
  first_seen_at: number
  last_seen_at: number
  updated_at: number
}

export type SlangRun = {
  id: number
  group_key: string
  started_at: number
  duration_ms: number
  messages: number
  added: number
  updated: number
  dropped: number
  detail: string
  error: string | null
}

/** GET /api/slang?group=: every group, and the chosen one's terms and recent learning runs. */
export type SlangView = {
  groups: { key: string; name: string; learn: boolean; learned_at: number | null; terms: number }[]
  group: string
  settings: { provider_id: string | null; default_provider: string | null; provider_loaded: boolean }
  entries: SlangEntry[]
  runs: SlangRun[]
}

/** GET /api/bots/{id}/groups: the QQ groups the bot's account is in. */
export type BotGroup = { channel_id: number; group_id: string; name: string; joined: boolean }
/** fresh = read from QQ just now; otherwise the list stored when the account last connected. */
export type BotGroups = { fresh: boolean; groups: BotGroup[] }

/** POST /api/bots/{id}/channels: take part in another group. Answers 201 {ok: true, channel_id}. */
export type ChannelCreate = { channel_id: number; activity: Activity; persona_id?: number }
export type PrivateCreate = { user_id: string; persona_id?: number }

// ---- Models (/api/providers) ----

export type ProviderKind = 'openai_chat' | 'openai_responses' | 'anthropic' | 'codex'

/** Options the backend accepts, per kind (anything else is rejected). Omitted = default. */
export type ProviderOptions = {
  /** All kinds: "" or http(s)://host:port. */
  proxy?: string
  /** All kinds: 1–300 seconds (default 40). */
  timeout?: number
  /** openai_chat only: 0–2; omitted = the model's default. */
  temperature?: number
  /** openai_chat, openai_responses and anthropic: a JSON object merged into the request body. */
  extra_body?: Record<string, unknown>
  /** openai_responses only (default "text"). */
  input_format?: 'text' | 'messages'
  /** anthropic only (default "x-api-key"): how the key is sent; some relays want a Bearer header. */
  auth_header?: 'x-api-key' | 'bearer'
  /** anthropic only (default "low"): how much the model thinks before replying. */
  effort?: 'low' | 'medium' | 'high'
  /** codex only (default ~/.codex/auth.json). */
  auth_file?: string
}

/** POST /api/providers/models: the model names the interface offers, or why it could not be asked. */
export type ProviderModels = { ok: true; models: string[] } | { ok: false; error: string }

export type ProviderUser = { bot_id: string; name: string; mode: BotMode }

/** GET /api/providers (sorted by id); POST and PATCH answer one of these. The key value is never sent. */
export type ProviderView = {
  id: string
  kind: ProviderKind
  base_url: string
  model: string
  /** Stored options as-is (may hold keys that no longer apply). */
  options: Record<string, unknown>
  /** Name of the .env entry holding the key; "" for codex. Never shown to the user. */
  key_ref: string
  key_set: boolean
  /** The key comes from a system environment variable, so editing it here would not take effect. */
  key_from_env: boolean
  /** Other models using the same key entry: changing the key changes it for them too. */
  key_shared_with: string[]
  used_by: ProviderUser[]
  /** null when the model is not loaded (see build_error). */
  health: ProviderHealth | null
  build_error: string | null
}

/** POST /api/providers → 201 ProviderView. 409 duplicate id or key name in use. */
export type ProviderCreate = {
  id: string
  kind: ProviderKind
  base_url: string
  model: string
  options?: ProviderOptions
  key?: string
}

/** PATCH /api/providers/{id} → 200 ProviderView. `options` replaces the stored options wholesale. */
export type ProviderPatch = {
  kind?: ProviderKind
  base_url?: string
  model?: string
  options?: ProviderOptions
  key?: string
  clear_key?: boolean
}

/** POST /api/providers/{id}/test (a model that failed to load answers ok: false with its error). */
export type ProviderTestResult = { ok: boolean; text?: string; latency_ms?: number; error?: string }

// ---- Personas (/api/personas) ----

export type PersonaUse = { bot_id: string; bot_name: string; channel_id: number; channel_name: string }

/** GET /api/personas (sorted by name); POST (201) and PATCH answer one of these. */
export type Persona = {
  id: number
  name: string
  content: string
  /** 说话示例, up to 1000 characters; "" when there are none. */
  examples: string
  updated_at: number
  used_by: PersonaUse[]
}

/** POST /api/personas. name 1–40, content 1–4000 code points; 409 duplicate name. */
export type PersonaCreate = { name: string; content: string }

/** PATCH /api/personas/{id}: at least one field; 409 when renamed onto an existing name. */
export type PersonaPatch = { name?: string; content?: string; examples?: string }

// ---- Usage (/api/usage?days=N) ----

export type UsageDays = 1 | 7 | 14

export type UsageProvider = {
  provider_id: string
  /** Seen in the call log but no longer configured. */
  deleted: boolean
  /** Attempts: a retry counts again. calls = ok + errors. */
  calls: number
  ok: number
  errors: number
  /** 0–1, null when calls = 0. */
  error_rate: number | null
  /** Successful calls only; null when there are none. */
  avg_latency_ms: number | null
  p95_latency_ms: number | null
  prompt_tokens: number
  completion_tokens: number
  /** Successful calls that reported token counts; 0 = this model does not report them. */
  token_calls: number
}

export type UsageBot = {
  bot_id: string
  name: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  outcomes: Record<Outcome, number>
}

export type UsageDay = {
  /** Local date, YYYY-MM-DD. */
  day: string
  provider_id: string
  calls: number
  errors: number
  prompt_tokens: number
  completion_tokens: number
}

export type UsageError = {
  at: number
  provider_id: string
  /** null for 测试一下 calls. */
  bot_id: string | null
  bot_name: string | null
  error: string
}

export type Usage = {
  /** Unix seconds: local midnight (days − 1) days ago. */
  since: number
  days: UsageDays
  providers: UsageProvider[]
  bots: UsageBot[]
  /** Every day in range for every model with calls in range, zero-filled. */
  daily: UsageDay[]
  /** Newest first, at most 20. */
  recent_errors: UsageError[]
}

// ---- 监控 (/api/watches) ----

/** feed = 订阅 (RSS/Atom), page = 网页变化, price = 价格. */
export type WatchKind = 'feed' | 'page' | 'price'
/** preview = 试运行 (the message is only recorded), push = 推送 (really sent). */
export type WatchMode = 'preview' | 'push'
export type WatchTargetKind = 'group' | 'private'
/** baseline: the first check after adding (or after changing what is watched) only remembers what is there. */
export type WatchOutcome = 'baseline' | 'unchanged' | 'changed' | 'error'
/**
 * What became of the run's message: sent, recorded only (试运行), held back (the bot is paused), or the send
 * failed / its result is unknown. null: no message, or one not sent for another reason (see `detail`).
 */
export type WatchDelivered = 'sent' | 'preview' | 'paused' | 'failed' | 'unknown'

/** One check (GET /api/watches/{id}/runs, POST /api/watches/{id}/run, SSE "watch"). */
export type WatchRun = {
  id: number
  started_at: number
  duration_ms: number
  outcome: WatchOutcome
  /** New entries (订阅) or 1 for a change. */
  changes: number
  /** What happened, in Chinese. */
  detail: string
  /** The text pushed, or that would have been. */
  message: string | null
  delivered: WatchDelivered | null
  error: string | null
}

/** GET /api/watches (sorted by name); POST (201) and PATCH answer one of these. */
export type WatchView = {
  id: number
  name: string
  kind: WatchKind
  url: string
  /** CSS selector; "" = the whole page (always "" for 订阅). */
  selector: string
  /** 价格 only: push only when the new price is at or below it. */
  threshold: number | null
  interval_minutes: number
  bot_id: string
  bot_name: string
  target_kind: WatchTargetKind
  /** QQ group number, or the QQ number for 私聊. */
  target_id: string
  /** The group's name, or 「私聊 <id>」. */
  target_name: string
  /** The model that rewrites the message; null = the plain list. */
  provider_id: string | null
  instructions: string
  use_proxy: boolean
  mode: WatchMode
  enabled: boolean
  last_run_at: number | null
  next_run_at: number | null
  /** A check is in progress right now (scheduled or 立即检查). */
  running: boolean
  last_run: WatchRun | null
  /** The newest run that has a message (sent or would have been), even if later checks were quiet. */
  last_message: WatchRun | null
  counts: { runs_7d: number; changes_7d: number; errors_7d: number }
}

/**
 * POST /api/watches → 201 WatchView. name 1–40; url http(s) ≤ 1000; selector ≤ 300, required for 价格;
 * threshold 价格 only, > 0; interval 5–1440; for a group target the bot's account must be in it; a 私聊 target is
 * 5–12 digits; instructions ≤ 500.
 */
export type WatchCreate = {
  name: string
  kind: WatchKind
  url: string
  selector?: string
  threshold?: number | null
  interval_minutes: number
  bot_id: string
  target_kind: WatchTargetKind
  target_id: string
  provider_id?: string | null
  instructions?: string
  use_proxy?: boolean
  mode?: WatchMode
  enabled?: boolean
}

/** PATCH /api/watches/{id} → WatchView: only what changed. Changing kind, url or selector starts over (baseline). */
export type WatchPatch = Partial<WatchCreate>

/** GET /api/watches/targets: for each bot, the groups its account is in. */
export type WatchTarget = { bot_id: string; bot_name: string; groups: { group_id: string; name: string }[] }

/** SSE "watch": a check finished. */
export type WatchEvent = { watch_id: number; run: WatchRun }

/** GET /api/auth (no sign-in needed): what the sign-in screen can offer this browser. */
export type AuthInfo = {
  signed_in: boolean
  password_set: boolean
  local: boolean
  /** How this machine opens the console without a password, e.g. "双击 启动.bat". */
  start_hint?: string
}

/** A browser that signed in with the password. */
export type RemoteDevice = {
  id: string
  ip: string
  agent: string
  created_at: number
  last_seen_at: number
  /** The browser asking. */
  current: boolean
}

/** GET /api/remote: opening the console from other devices. */
export type RemoteView = {
  /** Other devices can connect now. */
  enabled: boolean
  /** What the next start will do (.env NEST_REMOTE); differs from `enabled` until nest restarts. */
  configured: boolean
  port: number
  /** This machine's local-network and Tailscale addresses, as links. */
  addresses: { url: string; kind: 'lan' | 'tailscale' }[]
  /** Its names (Bonjour .local, Tailscale, NEST_ALLOWED_HOSTS), as links. */
  names: { url: string; name: string }[]
  password_set: boolean
  devices: RemoteDevice[]
}

// ---- 项目资料 (/api/knowledge) ----

export type KnowledgeBaseView = {
  id: number
  name: string
  folder: string
  sections: number
  files: Record<string, number>
  loaded_at: number | null
  vectors: boolean
  problem: string
  forward_to: string
  /** The project's source checkout read when the docs do not answer (看源码), "" for none. Absent on an
   * older nest. */
  source_dir?: string
  /** That checkout now: null when none is set. */
  source?: { ready: boolean; commit: string; date: string; files: number } | null
  groups: string[]
}

export type KnowledgeView = {
  bases: KnowledgeBaseView[]
  groups: { group_key: string; name: string; base_id: number | null }[]
  models: { embed: string; rerank: string; embed_up: boolean; rerank_up: boolean }
}

export type AskStep = { id: string; title: string; score: number }

export type AskResult = {
  found: boolean
  best: number
  reranked: boolean
  degraded: string
  took_ms: number
  sections: { id: string; title: string; file: string; score: number; text: string }[]
  steps: Record<string, AskStep[]>
}

export type KnowledgeGap = {
  id: number
  base_id: number
  group_key: string
  group_name: string
  asker: string
  question: string
  answer: string
  reason: 'not_found' | 'uncertain'
  status: 'open' | 'answered' | 'ignored'
  created_at: number
  forwarded_at: number | null
  note_id: number | null
  /** 看源码: what the bot worked out from the source ("" when it did not read it), whether it was sure
   * (then it posted it in the group), and the files it rests on ("\n"-joined). Absent on an older nest. */
  analysis?: string
  analysis_sure?: number
  analysis_files?: string
}

export type KnowledgeNote = { id: number; title: string; body: string; created_at: number }

export type GapsView = { gaps: KnowledgeGap[]; notes: KnowledgeNote[] }
