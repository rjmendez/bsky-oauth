"""bsky-oauth command line: one-time setup and checks for a profile.

    bsky-oauth install
    bsky-oauth --config profiles.toml --profile poster keygen
    bsky-oauth --config profiles.toml --profile poster metadata <out dir>
    bsky-oauth --config profiles.toml --profile poster login <handle>
    bsky-oauth --config profiles.toml --profile poster whoami
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .client import Account, BskyError, Profile, install


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bsky-oauth")
    ap.add_argument("--config", default=os.environ.get("BSKY_OAUTH_CONFIG"), help="profiles TOML (env BSKY_OAUTH_CONFIG)")
    ap.add_argument("--profile", default=None)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="npm ci in the bundled Node folder")
    sub.add_parser("keygen", help="create the profile's client signing key")
    m = sub.add_parser("metadata", help="write client-metadata.json, jwks.json, callback.html to publish")
    m.add_argument("out_dir")
    lg = sub.add_parser("login", help="one-time browser sign-in")
    lg.add_argument("handle")
    sub.add_parser("whoami", help="refresh the session, show account and scope")
    args = ap.parse_args(argv)
    try:
        if args.command == "install":
            install()
            return 0
        if not args.config or not args.profile:
            raise BskyError("--config and --profile are required for this command")
        acct = Account(Profile.from_toml(Path(args.config), args.profile))
        if args.command == "login":
            return acct.login(args.handle)
        result = {"keygen": acct.keygen, "whoami": acct.whoami}.get(args.command)
        out = result() if result else acct.metadata(Path(args.out_dir))
        print(json.dumps(out, indent=2))
        return 0
    except BskyError as e:
        print(f"bsky-oauth {args.command}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
