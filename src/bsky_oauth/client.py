"""Profiles and accounts: one Bluesky bot = one profile (its own OAuth client, scope, key and session).

Nothing here speaks OAuth or touches a key. Every authenticated step runs in the bundled Node helper (node/bsky.mjs), which is glue
around Bluesky's official @atproto/oauth-client-node and @atproto/api; this module only starts it with a profile's settings and maps
its exit codes to exceptions:
  2 -> BskyError       usage or configuration
  3 -> NothingSent     stopped before the write call (no session, refresh refused, an upload failed): nothing changed, safe to retry
  4 -> UnknownOutcome  the write call itself (createRecord / deleteRecord) failed: it may or may not have taken effect
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

NODE_DIR = Path(__file__).resolve().parent / "node"
HELPER = NODE_DIR / "bsky.mjs"


class BskyError(Exception):
    """A configuration or usage problem, or a helper failure that is neither of the two below."""


class NothingSent(BskyError):
    """The helper stopped before the write call: nothing changed on the account."""


class UnknownOutcome(BskyError):
    """The write call itself failed: check the account before retrying."""


@dataclass(frozen=True)
class Profile:
    """One OAuth client. client_id is the https URL of its published metadata; state_dir holds its private key and session."""

    name: str
    client_id: str
    redirect_uri: str
    scope: str
    state_dir: Path
    client_name: str = "bsky-oauth client"

    @classmethod
    def from_toml(cls, path: Path, name: str) -> Profile:
        """[profiles.<name>] from a TOML file: client_id, redirect_uri, scope, state_dir, optional client_name."""
        data = tomllib.loads(Path(path).read_text(encoding="utf-8")).get("profiles", {})
        if name not in data:
            raise BskyError(f"no profile {name!r} in {path} (have: {', '.join(sorted(data)) or 'none'})")
        p = data[name]
        missing = [k for k in ("client_id", "redirect_uri", "scope", "state_dir") if not p.get(k)]
        if missing:
            raise BskyError(f"profile {name!r} in {path} lacks {', '.join(missing)}")
        return cls(name=name, client_id=p["client_id"], redirect_uri=p["redirect_uri"], scope=p["scope"], state_dir=Path(p["state_dir"]),
                   client_name=p.get("client_name", cls.client_name))


@dataclass(frozen=True)
class PostRef:
    uri: str
    cid: str


@dataclass(frozen=True)
class Image:
    path: Path
    alt: str
    width: int
    height: int


class Account:
    """A profile's view of one Bluesky account, through the Node helper."""

    def __init__(self, profile: Profile, *, node: str | None = None, helper: Path | None = None):
        self.profile = profile
        self.node = node or os.environ.get("BSKY_OAUTH_NODE") or "node"
        self.helper = Path(helper or HELPER)

    def env(self) -> dict:
        p = self.profile
        return {**os.environ, "BSKY_OAUTH_DIR": str(p.state_dir), "BSKY_OAUTH_CLIENT_ID": p.client_id,
                "BSKY_OAUTH_REDIRECT_URI": p.redirect_uri, "BSKY_OAUTH_SCOPE": p.scope, "BSKY_OAUTH_CLIENT_NAME": p.client_name}

    def run(self, args: list[str], *, stdin: str | None = None, interactive: bool = False) -> subprocess.CompletedProcess:
        """Run the helper. interactive: share this terminal (sign-in) and return without raising."""
        if self.helper == HELPER and not (NODE_DIR / "node_modules").is_dir():
            raise BskyError(f"the Node dependencies are not installed: run `bsky-oauth install` (npm ci in {NODE_DIR})")
        cmd = [self.node, str(self.helper), *args]
        try:
            if interactive:
                return subprocess.run(cmd, env=self.env())
            return subprocess.run(cmd, env=self.env(), input=(stdin or "").encode("utf-8"), capture_output=True)
        except FileNotFoundError as e:
            raise BskyError(f"cannot run {self.node} (Node >= 22 required; env BSKY_OAUTH_NODE): {e}") from e

    def _call(self, args: list[str], payload: dict | None = None) -> dict:
        r = self.run(args, stdin=json.dumps(payload) if payload is not None else None)
        err = r.stderr.decode("utf-8", "replace").strip()
        if r.returncode == 3:
            raise NothingSent(err or "the helper stopped before the write call")
        if r.returncode == 4:
            raise UnknownOutcome(err or "the write call failed")
        if r.returncode != 0:
            raise BskyError(f"helper exit {r.returncode}: {err}")
        return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])

    # one-time setup -------------------------------------------------------------------------------------------------------

    def keygen(self) -> dict:
        """Create this profile's client signing key (never replaces one)."""
        return self._call(["keygen"])

    def metadata(self, out_dir: Path) -> dict:
        """Write client-metadata.json, jwks.json (public key only) and callback.html for publishing at the profile's client_id."""
        return self._call(["metadata", str(out_dir)])

    def login(self, handle: str) -> int:
        """Interactive browser sign-in in this terminal; returns the helper's exit code."""
        return self.run(["login", handle], interactive=True).returncode

    # use ------------------------------------------------------------------------------------------------------------------

    def whoami(self) -> dict:
        """Refresh the session and return {did, handle, scope, expiresAt}."""
        return self._call(["whoami"])

    def post(self, text: str, images: list[Image] = (), *, langs: tuple[str, ...] = ("en",)) -> PostRef:
        """A post with up to 4 JPEG images; links and #tags become facets (@atproto/api RichText)."""
        out = self._call(["post"], {"text": text, "langs": list(langs),
                                    "images": [{"path": str(i.path), "alt": i.alt, "width": i.width, "height": i.height} for i in images]})
        return PostRef(out["uri"], out["cid"])

    def delete(self, uri: str) -> None:
        """Delete one of the signed-in account's records by at:// URI (needs a delete scope for its collection)."""
        self._call(["delete"], {"uri": uri})


def install(npm: str | None = None) -> None:
    """npm ci in the bundled node/ folder: exact versions from package-lock.json."""
    npm = npm or shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise BskyError("npm not found (Node >= 22 with npm required)")
    r = subprocess.run([npm, "ci", "--no-fund", "--no-audit"], cwd=NODE_DIR)
    if r.returncode != 0:
        raise BskyError(f"npm ci failed in {NODE_DIR} (exit {r.returncode})")
