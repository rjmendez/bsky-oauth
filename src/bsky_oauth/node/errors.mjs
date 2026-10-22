// Error classes and the exit-code contract shared by bsky.mjs and its tests (see bsky.mjs's header for the meaning of each code).
export class Usage extends Error {}
export class NothingSent extends Error {}

// Commands whose final step writes to the account: a failure there leaves the outcome unknown.
export const WRITES = new Set(['post', 'delete', 'delete-many'])

export function exitCode(cmd, err) {
  if (err instanceof Usage) return 2
  if (err instanceof NothingSent) return 3
  return WRITES.has(cmd) ? 4 : 1
}

// One failed delete inside a batch: a rate limit stops the batch, anything else is reported and the batch goes on.
export function deleteFailure(err) {
  const msg = String(err?.message || err)
  return { status: err?.status === 429 || /rate ?limit/i.test(msg) ? 'rate_limited' : 'failed', error: msg }
}
