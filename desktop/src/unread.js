'use strict'

// 托盘未读数: what the companions said in the chat app while you were not looking at the
// chat window. Counted from nest's live events in the main process, so it counts with the window closed
// too; looking at the chat window clears it.

const KEEP_IDS = 500 // a 桌面群 message arrives once per member: counted once, by its id

class Unread {
  /** watching(): whether you are looking at the chat window now; onChange(count) when the count changes. */
  constructor({ watching, onChange }) {
    this.watching = watching
    this.onChange = onChange
    this.count = 0
    this.ids = new Set()
  }

  /** A "message" event of nest. Her words in the chat app (ids "app:…") count; anything else does not. */
  heard(message) {
    if (!message || !message.from_self) return
    const id = String(message.external_id || '')
    if (!id.startsWith('app:') || this.ids.has(id)) return
    this.ids.add(id)
    if (this.ids.size > KEEP_IDS) this.ids.delete(this.ids.values().next().value)
    if (this.watching()) return
    this.count += 1
    this.onChange(this.count)
  }

  clear() {
    if (this.count === 0) return
    this.count = 0
    this.onChange(0)
  }
}

module.exports = { Unread }
