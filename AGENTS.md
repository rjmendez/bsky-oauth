# AGENTS.md

Instructions for coding agents working in this repository.

## Standing rules (owner decisions)

**Never roll your own OAuth or crypto (owner, 2026-10-22).** Every authenticated step (PAR, PKCE, DPoP, client assertions,
token refresh and rotation, key generation, rich-text facets) is done by Bluesky's official libraries
(`@atproto/oauth-client-node`, `@atproto/jwk-jose`, `@atproto/api`) inside `src/bsky_oauth/node/bsky.mjs`. That file and the
Python side are glue: they pass settings, read files and map exit codes. Do not add JWT, signing, hashing or token handling code
here; upgrade the pinned library versions instead (`package-lock.json`, `npm ci`).

**One bot = one profile = one OAuth client with the smallest scope that does its job.** Confidential clients only (a public
client's session is capped at two weeks). Each profile's metadata and PUBLIC key are published; its private key and session stay
in its `state_dir` (owner-only ACL). Never publish, print or log a private key, token or session file.

**Writes are the caller's to approve.** This library performs a write only when its caller asks; callers decide approval and
keep their own ledger. Exit code 3 / `NothingSent` means nothing changed; exit 4 / `UnknownOutcome` means check the account
before retrying, never retry blind.

## Test-writing rules

These apply to every test added or edited in this repo, no exceptions:

1. **Assert the exact value the claim names.** Not "key present", not a range,
   not `>= 0`. If the right answer is known, pin it.
2. **Never stub, re-implement or source-grep the unit under test.** Stub only its
   dependencies.
3. **Every negative test has a positive twin on the same fixture.**
4. **A stub that must not be called records its calls; assert the list is empty.**
5. **Error, degraded or fail-open output is not a pass.** Fake the dependency so
   the success branch actually runs and its values get asserted.
6. **`pytest.raises(SpecificError, match=...)`.** A bare/broad exception class
   without a real `match=` is rejected.
7. **The fixture must be able to tell right from wrong**: distinct values,
   boundary cases, winners that aren't first-by-accident.
8. **Never pin a known bug as expected.** Use `xfail(strict=True, reason=...)`
   instead.
9. **No state leaks.** `monkeypatch` for globals/env, `tmp_path` for files.
10. **No off-by-default tests.** A test gated behind an opt-in env var or marker
    that nothing turns on in normal use never runs, which makes it no test. If a
    real external dependency (hardware, a license-gated binary, a slow model) is
    genuinely required, still prefer making it run by default against a small
    committed fixture over skipping it — see `tests/test_preclean_live.py` for the
    pattern: a real raw file + real model, cropped down so it still runs in every
    `pytest` invocation at low enough cost to not be worth skipping.
11. **Prove it before claiming coverage.** Apply the obvious mutation to the code
    (delete the guard, return the constant, invert the check), confirm the test
    goes red, revert. State which mutation was run.
12. **No test without an assertion; no `assert True`; no swallowed `AssertionError`.**

### Enforcement

`scripts/check_test_honesty.py` mechanically rejects rules 1 (broad-raises),
8 (xfail-not-strict), 10 (env-skipif) and 12 (no-assertions/assert-true/or-true/
swallowed-assert):

    python scripts/check_test_honesty.py              # whole repo
    python scripts/check_test_honesty.py path/to/test.py

Its allowlist (`scripts/test_honesty_allowlist.toml`) starts empty and should stay
that way — fix the test rather than adding an entry.

## Running tests

`bsky-oauth install` (or `npm ci` in `src/bsky_oauth/node`), then `pytest`. The helper tests run the real Node helper and the real
official libraries (no network): keygen, metadata, facets, and every failure path that stops before a network write.
