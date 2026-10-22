// Error classes and the exit-code contract shared by bsky.mjs and its tests (see bsky.mjs's header for the meaning of each code).
export class Usage extends Error {}
export class NothingSent extends Error {}

// Commands whose final step writes to the account: a failure there leaves the outcome unknown.
export const WRITES = new Set(['post', 'delete'])

export function exitCode(cmd, err) {
  if (err instanceof Usage) return 2
  if (err instanceof NothingSent) return 3
  return WRITES.has(cmd) ? 4 : 1
}
