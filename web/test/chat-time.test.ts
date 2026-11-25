import assert from 'node:assert/strict'
import test from 'node:test'

import { rowsOf } from '../src/lib/chat-time.ts'

const said = (id: number, sender_id: string, from_self: boolean, sent_at: number) => ({ id, sender_id, from_self, sent_at })
const firsts = (messages: ReturnType<typeof said>[]) =>
  rowsOf(messages).flatMap((row) => (row.kind === 'message' ? [row.first] : []))

test('in a group each one who speaks shows who it is, also right after another companion', () => {
  const at = 1_800_000_000
  // The group as the first member sees it: her own lines are from_self, the user and the others are not.
  const group = [said(1, 'desk', false, at), said(2, '10002', false, at + 5), said(3, '10002', false, at + 6)]
  assert.deepEqual(firsts([...group, said(4, '10003', false, at + 9), said(5, '10001', true, at + 12)]), [
    true,
    true,
    false,
    true,
    true,
  ])
})

test('a run by one of two in a chat shows the avatar once, and again after a pause', () => {
  const at = 1_800_000_000
  assert.deepEqual(firsts([said(1, 'desk', false, at), said(2, 'desk', false, at + 2), said(3, 'desk', false, at + 900)]), [
    true,
    false,
    true,
  ])
})
