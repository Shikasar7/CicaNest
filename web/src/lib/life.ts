/** 小窝和她的日子. */

import type { Pose } from '@/lib/home-stage'

export type LifeBlock = {
  from: string
  to: string
  room: string
  doing: string
  mood: string
  share: string
  /** How she is seen in the 小窝. Absent on an older nest. */
  pose?: Pose
}

export type LifeProfile = {
  wake: string
  sleep: string
  rooms: string[]
  hobbies: string[]
  lately: string
  habits: string
  friends: string
  home: boolean
  /** The day you first talked (YYYY-MM-DD), '' = as far as nest knows. */
  since?: string
}

export type LifePerson = {
  bot_id: string
  name: string
  home: boolean
  profile: LifeProfile
  /** The day you first talked, set or found; '' when not known. Absent on an older nest. */
  since?: string
  /** 「认识 100 天」 when today is such a day. */
  milestone?: string
  day: LifeBlock[]
  now: LifeBlock | null
}

export type LifeView = {
  on: boolean
  day: string
  planned: boolean
  rooms: string[]
  people: LifePerson[]
  moments: { id: number; bot_id: string; at: number; text: string; source: string }[]
}
