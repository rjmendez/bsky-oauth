"""bsky_oauth: Bluesky (atproto) OAuth for Python bots, through Bluesky's official TypeScript client.

    from bsky_oauth import Account, Image, Profile
    acct = Account(Profile.from_toml("profiles.toml", "poster"))
    ref = acct.post("Hello #nature", [Image(path, "alt text", 2000, 1333)])

Each bot is a Profile: its own confidential OAuth client (published metadata + public key), its own minimal scope, its own private
key and session on disk. See README.md.
"""
from .client import Account, BatchInterrupted, BskyError, Image, NothingSent, PostRef, Profile, UnknownOutcome, install

__all__ = ["Account", "BatchInterrupted", "BskyError", "Image", "NothingSent", "PostRef", "Profile", "UnknownOutcome", "install"]
