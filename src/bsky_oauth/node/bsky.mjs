#!/usr/bin/env node
// bsky-oauth's Node half: built on Bluesky's official OAuth client (@atproto/oauth-client-node) and API (@atproto/api). This file
// is glue only: OAuth (PAR, PKCE, DPoP, client assertions, token refresh and rotation), key generation (JoseKey) and rich-text
// facets (RichText) all come from those libraries; nothing here implements a protocol or a crypto step. The Python package
// (bsky_oauth.Account) runs it with one profile's settings in the environment:
//   BSKY_OAUTH_DIR, BSKY_OAUTH_CLIENT_ID, BSKY_OAUTH_REDIRECT_URI, BSKY_OAUTH_SCOPE, BSKY_OAUTH_CLIENT_NAME [, BSKY_OAUTH_APPVIEW]
//
//   node bsky.mjs keygen                 create the client signing key (ES256) in BSKY_OAUTH_DIR; never overwrites
//   node bsky.mjs metadata <out dir>     write client-metadata.json + jwks.json + callback.html to publish
//   node bsky.mjs login <handle>         one-time browser sign-in; paste the callback page's address when asked
//   node bsky.mjs whoami                 restore (and refresh) the session, print {did, handle, scope, expiresAt}
//   node bsky.mjs post                   stdin {text, langs, images:[{path, alt, width, height}]} -> stdout {uri, cid}
//   node bsky.mjs delete                 stdin {uri} (one of the signed-in account's records) -> stdout {deleted: uri}
//
// Exit codes: 0 ok; 2 usage/config; 3 failed before the write call (session, refresh, upload): nothing changed; 4 the write call
// (createRecord / deleteRecord) itself failed, so whether it took effect is unknown.
import fs from 'node:fs'
import path from 'node:path'
import readline from 'node:readline/promises'
import { NodeOAuthClient } from '@atproto/oauth-client-node'
import { JoseKey } from '@atproto/jwk-jose'
import { Agent, AtUri, RichText } from '@atproto/api'
import { NothingSent, Usage, exitCode } from './errors.mjs'

const env = (name, fallback) => process.env[name] || fallback
const OAUTH_DIR = env('BSKY_OAUTH_DIR')
const CLIENT_ID = env('BSKY_OAUTH_CLIENT_ID')
const REDIRECT_URI = env('BSKY_OAUTH_REDIRECT_URI')
const SCOPE = env('BSKY_OAUTH_SCOPE')
const CLIENT_NAME = env('BSKY_OAUTH_CLIENT_NAME', 'bsky-oauth client')
// Public, unauthenticated reads (profile, handle resolution) never use the session, so a profile's scope can stay minimal
const publicAgent = () => new Agent(env('BSKY_OAUTH_APPVIEW', 'https://public.api.bsky.app'))


function need(value, name) {
  if (!value) throw new Usage(`${name} is not set`)
  return value
}

const file = (name) => path.join(need(OAUTH_DIR, 'BSKY_OAUTH_DIR'), name)

function writeAtomic(target, text) {
  fs.mkdirSync(path.dirname(target), { recursive: true })
  const tmp = `${target}.tmp`
  fs.writeFileSync(tmp, text, { mode: 0o600 })
  fs.renameSync(tmp, target) // replaces: a refreshed token set must never be half-written (refresh tokens are single-use)
}

// A JSON-file key/value store: the session and in-flight sign-in state, as the client's store interfaces require.
function jsonStore(name) {
  const target = file(name)
  const read = () => (fs.existsSync(target) ? JSON.parse(fs.readFileSync(target, 'utf8')) : {})
  return {
    async get(key) { return read()[key] },
    async set(key, value) { const all = read(); all[key] = value; writeAtomic(target, JSON.stringify(all, null, 1)) },
    async del(key) { const all = read(); delete all[key]; writeAtomic(target, JSON.stringify(all, null, 1)) },
  }
}

// Cross-process lock around token refresh (the daily task and a manual run must not spend the same refresh token twice).
async function requestLock(key, fn) {
  const lock = file('refresh.lock')
  const deadline = Date.now() + 60_000
  for (;;) {
    try { fs.closeSync(fs.openSync(lock, 'wx')); break } catch (e) {
      if (e.code !== 'EEXIST') throw e
      if (Date.now() > deadline) throw new Error(`lock ${lock} held for over 60 s; remove it if no other run is active`)
      await new Promise((r) => setTimeout(r, 250))
    }
  }
  try { return await fn() } finally { fs.rmSync(lock, { force: true }) }
}

async function loadKey() {
  const keyFile = file('client_key.jwk.json')
  if (!fs.existsSync(keyFile)) throw new Usage(`no client key at ${keyFile}: run keygen first`)
  return JoseKey.fromImportable(JSON.parse(fs.readFileSync(keyFile, 'utf8')))
}

async function makeClient() {
  const key = await loadKey()
  return new NodeOAuthClient({
    clientMetadata: {
      client_id: need(CLIENT_ID, 'BSKY_OAUTH_CLIENT_ID'),
      client_name: CLIENT_NAME,
      client_uri: new URL('./', CLIENT_ID).href,
      redirect_uris: [need(REDIRECT_URI, 'BSKY_OAUTH_REDIRECT_URI')],
      grant_types: ['authorization_code', 'refresh_token'],
      response_types: ['code'],
      scope: need(SCOPE, 'BSKY_OAUTH_SCOPE'),
      application_type: 'web',
      token_endpoint_auth_method: 'private_key_jwt',
      token_endpoint_auth_signing_alg: 'ES256',
      dpop_bound_access_tokens: true,
      jwks_uri: new URL('jwks.json', CLIENT_ID).href, // published next to the metadata: the library's own client.jwks
    },
    keyset: [key],
    stateStore: jsonStore('states.json'),
    sessionStore: jsonStore('sessions.json'),
    requestLock,
  })
}

function account() {
  const f = file('account.json')
  if (!fs.existsSync(f)) throw new NothingSent('no Bluesky session yet for this profile: sign in once (login)')
  return JSON.parse(fs.readFileSync(f, 'utf8'))
}

const CALLBACK_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><title>Bluesky sign-in</title>
<style>body{font:16px system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem}
textarea{width:100%;height:7rem;font:13px monospace}button{font-size:1rem;padding:.4rem 1rem}</style></head>
<body><h1>Almost done</h1>
<p>Copy this address and paste it into the terminal that is waiting for it (the sign-in command).
It only works together with that terminal's own sign-in keys, and nothing on this page sends it anywhere.</p>
<textarea id="u" readonly></textarea><p><button id="c">Copy</button> <span id="s"></span></p>
<script>
var u=document.getElementById('u'),s=document.getElementById('s');u.value=location.href;
document.getElementById('c').onclick=function(){u.select();
(navigator.clipboard?navigator.clipboard.writeText(u.value):Promise.reject()).then(function(){s.textContent='Copied.'},
function(){document.execCommand('copy');s.textContent='Copied.'})};
</script></body></html>
`

const commands = {
  async keygen() {
    const keyFile = file('client_key.jwk.json')
    if (fs.existsSync(keyFile)) throw new Usage(`${keyFile} exists; refusing to replace the client key`)
    const key = await JoseKey.generate(['ES256'], `bio-${new Date().toISOString().slice(0, 10)}`)
    fs.mkdirSync(path.dirname(keyFile), { recursive: true })
    fs.writeFileSync(keyFile, JSON.stringify(key.privateJwk), { mode: 0o600, flag: 'wx' })
    console.log(JSON.stringify({ created: keyFile, kid: key.kid }))
  },

  async metadata(outDir) {
    need(outDir, 'output folder')
    const client = await makeClient()
    const jwks = JSON.parse(JSON.stringify(client.jwks)) // what will actually be published
    for (const k of jwks.keys) if (k.d !== undefined) throw new Error('private key material in the jwks; nothing written')
    fs.mkdirSync(outDir, { recursive: true })
    fs.writeFileSync(path.join(outDir, 'client-metadata.json'), JSON.stringify(client.clientMetadata, null, 2) + '\n')
    fs.writeFileSync(path.join(outDir, 'jwks.json'), JSON.stringify(jwks, null, 2) + '\n')
    fs.writeFileSync(path.join(outDir, 'callback.html'), CALLBACK_HTML)
    console.log(JSON.stringify({ wrote: ['client-metadata.json', 'jwks.json', 'callback.html'].map((f) => path.join(outDir, f)) }))
  },

  async login(handle) {
    need(handle, 'handle')
    const client = await makeClient()
    const url = await client.authorize(handle, { scope: SCOPE })
    console.log(`\nOpen this address in a browser and approve the sign-in for ${handle}:\n\n${url}\n`)
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout })
    const closed = new Promise((_, reject) => rl.once('close', () => reject(new Usage('input closed before an address was pasted'))))
    const pasted = (await Promise.race([rl.question('Then paste the address of the page you land on: '), closed])).trim()
    rl.close()
    const { session } = await client.callback(new URL(pasted).searchParams)
    const info = await session.getTokenInfo()
    const profile = await publicAgent().getProfile({ actor: session.did })
    writeAtomic(file('account.json'), JSON.stringify({ did: session.did, handle: profile.data.handle }, null, 1))
    console.log(JSON.stringify({ did: session.did, handle: profile.data.handle, scope: info.scope, expiresAt: info.expiresAt }))
  },

  async whoami() {
    const { did } = account()
    const client = await makeClient()
    const session = await client.restore(did, true) // true: refresh now, so a dead session shows up here and not at post time
    const info = await session.getTokenInfo()
    const profile = await publicAgent().getProfile({ actor: did })
    console.log(JSON.stringify({ did, handle: profile.data.handle, scope: info.scope, expiresAt: info.expiresAt }))
  },

  async post() {
    const input = JSON.parse(fs.readFileSync(0, 'utf8'))
    let agent, embed, rt
    try {
      const { did } = account()
      const client = await makeClient()
      agent = new Agent(await client.restore(did, true))
      rt = new RichText({ text: input.text })
      await rt.detectFacets(publicAgent()) // links and tags; mention lookups (none expected) go to the public AppView
      const images = []
      for (const img of input.images) {
        const { data } = await agent.uploadBlob(fs.readFileSync(img.path), { encoding: 'image/jpeg' })
        images.push({ alt: img.alt, image: data.blob, aspectRatio: { width: img.width, height: img.height } })
      }
      embed = { $type: 'app.bsky.embed.images', images }
    } catch (e) {
      if (e instanceof Usage) throw e
      throw new NothingSent(String(e?.message || e))
    }
    const res = await agent.post({ text: rt.text, facets: rt.facets, langs: input.langs, embed, createdAt: new Date().toISOString() })
    console.log(JSON.stringify({ uri: res.uri, cid: res.cid }))
  },

  async delete() {
    const input = JSON.parse(fs.readFileSync(0, 'utf8'))
    let agent, at
    try {
      const { did } = account()
      at = new AtUri(input.uri)
      if (at.host !== did) throw new Usage(`${input.uri} is not a record of the signed-in account ${did}`)
      const client = await makeClient()
      agent = new Agent(await client.restore(did, true))
    } catch (e) {
      if (e instanceof Usage) throw e
      throw new NothingSent(String(e?.message || e))
    }
    await agent.com.atproto.repo.deleteRecord({ repo: at.host, collection: at.collection, rkey: at.rkey })
    console.log(JSON.stringify({ deleted: input.uri }))
  },
}

const [cmd, ...args] = process.argv.slice(2)
try {
  if (!commands[cmd]) throw new Usage(`usage: bsky.mjs ${Object.keys(commands).join('|')}`)
  await commands[cmd](...args)
} catch (e) {
  console.error(`bsky ${cmd}: ${e?.message || e}`)
  process.exit(exitCode(cmd, e))
}
