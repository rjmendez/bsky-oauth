# bsky-oauth

Bluesky (atproto) OAuth for Python bots. No passwords, no hand-rolled crypto: every authenticated step runs in Bluesky's official
`@atproto/oauth-client-node` (plus `@atproto/jwk-jose`, `@atproto/api`) through a small bundled Node helper. Python calls it.

Requires Python >= 3.11 and Node >= 22.

```
pip install -e .            # or pip install git+https://github.com/rjmendez/bsky-oauth
bsky-oauth install          # npm ci in the bundled node/ folder (exact versions from package-lock.json)
```

## Profiles

One bot = one profile = one confidential OAuth client with its own scope, key and session:

```toml
# profiles.toml
[profiles.poster]
client_id    = "https://your-username.github.io/your-oauth-repo/poster/client-metadata.json"
redirect_uri = "https://your-username.github.io/your-oauth-repo/callback.html"
scope        = "atproto repo:app.bsky.feed.post?action=create blob:image/*"
state_dir    = '/path/to/your/poster/oauth'
client_name  = "my bot's photo posts"

[profiles.scrubber]           # example: delete old posts/replies, likes and reposts; cannot create anything
client_id    = "https://your-username.github.io/your-oauth-repo/scrubber/client-metadata.json"
redirect_uri = "https://your-username.github.io/your-oauth-repo/callback.html"
scope        = "atproto repo:app.bsky.feed.post?action=delete repo:app.bsky.feed.like?action=delete repo:app.bsky.feed.repost?action=delete"
state_dir    = '/path/to/your/scrubber/oauth'
```

Why confidential: atproto caps a public client's session at two weeks; a confidential client's session has no fixed end (each
refresh token lasts up to 180 days and is replaced on every refresh), so a daily job never needs a new sign-in.

## Setup, once per profile

1. `bsky-oauth --config profiles.toml --profile poster keygen`: ES256 key in `state_dir` (restrict that folder to your user).
2. `bsky-oauth ... metadata out/`: `client-metadata.json`, `jwks.json` (public key only), `callback.html`. Publish them so that
   `client_id` serves the metadata (HTTPS, no port, `application/json`) and `jwks.json` sits next to it; `callback.html` goes at
   `redirect_uri`. GitHub Pages works.
3. `bsky-oauth ... login <handle>`: open the printed address, approve, paste the address of the callback page.
4. `bsky-oauth ... whoami`: refreshes the session and shows the account and granted scope.

## Use

```python
from bsky_oauth import Account, Image, NothingSent, Profile, UnknownOutcome

acct = Account(Profile.from_toml("profiles.toml", "poster"))
try:
    ref = acct.post("Lattice Orbweaver https://www.inaturalist.org/observations/1 #nature",
                    [Image("1.jpg", "A spider at the hub of its web.", 2000, 1333)])
except NothingSent:      # stopped before the write call: nothing changed, safe to retry later
    ...
except UnknownOutcome:   # the write call failed: check the account before any retry
    ...
```

`Account.delete(at_uri)` deletes one of the signed-in account's records (needs a delete scope for that collection).
`Account.delete_many(uris, pause_ms=300)` deletes many in one session and returns one result per URI (`deleted`, `failed`,
`rate_limited`; a rate limit stops the batch, URIs after it are left out). Foreign URIs are refused before anything is sent; if
the helper dies mid-batch, `BatchInterrupted.results` holds what was done.
Reading public data (your posts, replies, likes) needs no session at all: use the public AppView or `com.atproto.repo.listRecords`.
