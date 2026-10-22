"""bsky_oauth: profiles, the helper boundary and the real helper's offline paths. The real Node helper and Bluesky's official
libraries run in every test that names them (no network: each path stops before contacting a server); the recording fake helper
stands in only where a server answer would be needed."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bsky_oauth import Account, BatchInterrupted, BskyError, Image, NothingSent, PostRef, Profile, UnknownOutcome, client
from bsky_oauth.cli import main

SCOPE = "atproto repo:app.bsky.feed.post?action=create blob:image/*"


def profile(tmp_path, name="poster", scope=SCOPE) -> Profile:
    return Profile(name=name, client_id=f"https://example.github.io/clients/{name}/client-metadata.json",
                   redirect_uri="https://example.github.io/clients/callback.html", scope=scope, state_dir=tmp_path / name,
                   client_name=f"{name} bot")


PROFILES = """
[profiles.poster]
client_id = "https://example.github.io/c/poster/client-metadata.json"
redirect_uri = "https://example.github.io/c/callback.html"
scope = "atproto repo:app.bsky.feed.post?action=create blob:image/*"
state_dir = 'C:\\state\\poster'
client_name = "photo posts"

[profiles.scrubber]
client_id = "https://example.github.io/c/scrubber/client-metadata.json"
redirect_uri = "https://example.github.io/c/callback.html"
scope = "atproto repo:app.bsky.feed.post?action=delete"
state_dir = 'C:\\state\\scrubber'

[profiles.broken]
client_id = "https://example.github.io/c/broken/client-metadata.json"
scope = ""
"""


class TestProfile:
    def test_profiles_load_exactly_and_the_name_picks_one(self, tmp_path):
        (tmp_path / "p.toml").write_text(PROFILES, encoding="utf-8")
        assert Profile.from_toml(tmp_path / "p.toml", "poster") == Profile(
            "poster", "https://example.github.io/c/poster/client-metadata.json", "https://example.github.io/c/callback.html",
            "atproto repo:app.bsky.feed.post?action=create blob:image/*", Path(r"C:\state\poster"), "photo posts")
        s = Profile.from_toml(tmp_path / "p.toml", "scrubber")
        assert (s.scope, s.state_dir, s.client_name) == ("atproto repo:app.bsky.feed.post?action=delete", Path(r"C:\state\scrubber"),
                                                         "bsky-oauth client")

    def test_unknown_or_incomplete_profiles_are_refused_by_name(self, tmp_path):
        (tmp_path / "p.toml").write_text(PROFILES, encoding="utf-8")
        with pytest.raises(BskyError, match=r"no profile 'nope' in .* \(have: broken, poster, scrubber\)"):
            Profile.from_toml(tmp_path / "p.toml", "nope")
        with pytest.raises(BskyError, match=r"profile 'broken' in .* lacks redirect_uri, scope, state_dir"):
            Profile.from_toml(tmp_path / "p.toml", "broken")


FAKE_HELPER = """
import fs from 'node:fs'
const [cmd, ...args] = process.argv.slice(2)
const input = fs.readFileSync(0, 'utf8')
const seen = {cmd, args, input: input ? JSON.parse(input) : null, env: Object.fromEntries(Object.entries(process.env).filter(([k]) => k.startsWith('BSKY_OAUTH_')))}
fs.writeFileSync(process.env.FAKE_LOG, JSON.stringify(seen))
if (process.env.FAKE_EXIT !== '0') { console.error('bsky ' + cmd + ': ' + process.env.FAKE_MSG); process.exit(Number(process.env.FAKE_EXIT)) }
console.log('a log line before the result')
console.log(process.env.FAKE_OUT)
"""


class TestBoundary:
    def fake(self, tmp_path, monkeypatch, exit_code=0, out="{}", msg=""):
        script = tmp_path / "fake.mjs"
        script.write_text(FAKE_HELPER, encoding="utf-8")
        for k, v in {"FAKE_LOG": str(tmp_path / "log.json"), "FAKE_EXIT": str(exit_code), "FAKE_OUT": out, "FAKE_MSG": msg}.items():
            monkeypatch.setenv(k, v)
        return Account(profile(tmp_path), helper=script)

    def seen(self, tmp_path):
        return json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))

    def test_post_sends_the_request_with_the_profile_and_returns_the_ref(self, tmp_path, monkeypatch):
        acct = self.fake(tmp_path, monkeypatch, out='{"uri": "at://did:plc:me/app.bsky.feed.post/3a", "cid": "bafy"}')
        ref = acct.post("Ä spider #nature", [Image(Path("1.jpg"), "alt one", 2000, 1333)], langs=("en", "de"))
        assert ref == PostRef("at://did:plc:me/app.bsky.feed.post/3a", "bafy")
        assert self.seen(tmp_path) == {
            "cmd": "post", "args": [],
            "input": {"text": "Ä spider #nature", "langs": ["en", "de"], "images": [{"path": "1.jpg", "alt": "alt one", "width": 2000, "height": 1333}]},
            "env": {"BSKY_OAUTH_DIR": str(tmp_path / "poster"), "BSKY_OAUTH_CLIENT_ID": "https://example.github.io/clients/poster/client-metadata.json",
                    "BSKY_OAUTH_REDIRECT_URI": "https://example.github.io/clients/callback.html", "BSKY_OAUTH_SCOPE": SCOPE,
                    "BSKY_OAUTH_CLIENT_NAME": "poster bot"}}

    def test_delete_sends_the_uri(self, tmp_path, monkeypatch):
        acct = self.fake(tmp_path, monkeypatch, out='{"deleted": "at://did:plc:me/app.bsky.feed.post/3a"}')
        assert acct.delete("at://did:plc:me/app.bsky.feed.post/3a") is None
        assert (self.seen(tmp_path)["cmd"], self.seen(tmp_path)["input"]) == ("delete", {"uri": "at://did:plc:me/app.bsky.feed.post/3a"})

    def test_exit_codes_map_to_exceptions(self, tmp_path, monkeypatch):
        acct = self.fake(tmp_path, monkeypatch, 3, msg="refresh refused")
        with pytest.raises(NothingSent, match=r"^bsky post: refresh refused$"):
            acct.post("t")
        acct = self.fake(tmp_path, monkeypatch, 4, msg="createRecord timed out")
        with pytest.raises(UnknownOutcome, match=r"^bsky delete: createRecord timed out$") as e:
            acct.delete("at://x/y/z")
        assert not isinstance(e.value, NothingSent)
        acct = self.fake(tmp_path, monkeypatch, 2, msg="BSKY_OAUTH_SCOPE is not set")
        with pytest.raises(BskyError, match=r"^helper exit 2: bsky whoami: BSKY_OAUTH_SCOPE is not set$") as e:
            acct.whoami()
        assert type(e.value) is BskyError

    def test_delete_many_returns_each_result_in_order_and_sends_the_batch(self, tmp_path, monkeypatch):
        lines = [{"uri": "at://did:plc:me/app.bsky.feed.post/1", "status": "deleted"},
                 {"uri": "at://did:plc:me/app.bsky.feed.post/2", "status": "failed", "error": "InvalidRequest"},
                 {"uri": "at://did:plc:me/app.bsky.feed.post/3", "status": "rate_limited", "error": "Rate Limit Exceeded"}]
        acct = self.fake(tmp_path, monkeypatch, out="\n".join(json.dumps(x) for x in lines))
        uris = [x["uri"] for x in lines] + ["at://did:plc:me/app.bsky.feed.post/4"]
        assert acct.delete_many(uris, pause_ms=50) == lines
        assert (self.seen(tmp_path)["cmd"], self.seen(tmp_path)["input"]) == ("delete-many", {"uris": uris, "pause_ms": 50})

    def test_delete_many_interrupted_keeps_the_partial_results(self, tmp_path, monkeypatch):
        acct = self.fake(tmp_path, monkeypatch, 4, msg="socket hang up")
        script = tmp_path / "fake.mjs"
        script.write_text(FAKE_HELPER.replace("if (process.env.FAKE_EXIT !== '0')",
                                              "console.log(JSON.stringify({uri: 'at://did:plc:me/app.bsky.feed.post/1', status: 'deleted'}))\n"
                                              "if (process.env.FAKE_EXIT !== '0')"), encoding="utf-8")
        with pytest.raises(BatchInterrupted, match=r"^bsky delete-many: socket hang up$") as e:
            acct.delete_many(["at://did:plc:me/app.bsky.feed.post/1", "at://did:plc:me/app.bsky.feed.post/2"])
        assert e.value.results == [{"uri": "at://did:plc:me/app.bsky.feed.post/1", "status": "deleted"}]
        assert isinstance(e.value, UnknownOutcome)
        acct = self.fake(tmp_path, monkeypatch, 3, msg="no Bluesky session yet")
        with pytest.raises(NothingSent, match=r"no Bluesky session yet"):
            acct.delete_many(["at://did:plc:me/app.bsky.feed.post/1"])

    def test_missing_node_dependencies_are_named_before_anything_runs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(client, "NODE_DIR", tmp_path / "node")
        with pytest.raises(BskyError, match=r"Node dependencies are not installed: run `bsky-oauth install`"):
            Account(profile(tmp_path)).whoami()
        monkeypatch.setattr(client, "NODE_DIR", client.HELPER.parent)
        with pytest.raises(NothingSent, match="no Bluesky session yet"):
            Account(profile(tmp_path)).whoami()

    def test_a_missing_node_binary_is_a_clear_error(self, tmp_path):
        with pytest.raises(BskyError, match=r"cannot run no-such-node-binary \(Node >= 22 required"):
            Account(profile(tmp_path), node="no-such-node-binary").whoami()


class TestRealHelper:
    def test_keygen_and_metadata_publish_only_the_public_key(self, tmp_path):
        acct = Account(profile(tmp_path))
        assert acct.keygen()["created"] == str(tmp_path / "poster" / "client_key.jwk.json")
        with pytest.raises(BskyError, match=r"refusing to replace the client key"):
            acct.keygen()

        acct.metadata(tmp_path / "pub")
        meta = json.loads((tmp_path / "pub" / "client-metadata.json").read_text())
        jwks = json.loads((tmp_path / "pub" / "jwks.json").read_text())
        private = json.loads((tmp_path / "poster" / "client_key.jwk.json").read_text())
        assert {k: meta[k] for k in ("client_id", "jwks_uri", "redirect_uris", "scope", "token_endpoint_auth_method",
                                     "token_endpoint_auth_signing_alg", "dpop_bound_access_tokens", "application_type", "client_name")} == {
            "client_id": "https://example.github.io/clients/poster/client-metadata.json",
            "jwks_uri": "https://example.github.io/clients/poster/jwks.json",
            "redirect_uris": ["https://example.github.io/clients/callback.html"], "scope": SCOPE,
            "token_endpoint_auth_method": "private_key_jwt", "token_endpoint_auth_signing_alg": "ES256",
            "dpop_bound_access_tokens": True, "application_type": "web", "client_name": "poster bot"}
        assert [(k["kid"], k["x"], k["y"], "d" in k) for k in jwks["keys"]] == [(private["kid"], private["x"], private["y"], False)]
        assert "d" in private
        assert "location.href" in (tmp_path / "pub" / "callback.html").read_text()

    def test_two_profiles_get_two_different_keys(self, tmp_path):
        a, b = Account(profile(tmp_path, "poster")), Account(profile(tmp_path, "scrubber", "atproto repo:app.bsky.feed.post?action=delete"))
        a.keygen(), b.keygen()
        ka = json.loads((tmp_path / "poster" / "client_key.jwk.json").read_text())
        kb = json.loads((tmp_path / "scrubber" / "client_key.jwk.json").read_text())
        assert ka["x"] != kb["x"]

    @pytest.mark.parametrize("call", [lambda a: a.whoami(), lambda a: a.post("t"), lambda a: a.delete("at://did:plc:me/app.bsky.feed.post/3a")])
    def test_without_a_session_nothing_is_sent(self, tmp_path, call):
        with pytest.raises(NothingSent, match=r"no Bluesky session yet for this profile: sign in once \(login\)"):
            call(Account(profile(tmp_path)))

    def test_delete_refuses_another_accounts_record_before_any_network_call(self, tmp_path):
        acct = Account(profile(tmp_path))
        (tmp_path / "poster").mkdir()
        (tmp_path / "poster" / "account.json").write_text(json.dumps({"did": "did:plc:me", "handle": "me.example"}))
        with pytest.raises(BskyError, match=r"at://did:plc:someoneelse/app.bsky.feed.post/3a is not a record of the signed-in account did:plc:me"):
            acct.delete("at://did:plc:someoneelse/app.bsky.feed.post/3a")
        # the account's own record passes that check and stops at the next missing piece (no client key yet)
        with pytest.raises(BskyError, match=r"no client key at .*client_key\.jwk\.json: run keygen first"):
            acct.delete("at://did:plc:me/app.bsky.feed.post/3a")

    def test_delete_many_refuses_any_foreign_uri_before_any_network_call(self, tmp_path):
        acct = Account(profile(tmp_path))
        (tmp_path / "poster").mkdir()
        (tmp_path / "poster" / "account.json").write_text(json.dumps({"did": "did:plc:me", "handle": "me.example"}))
        with pytest.raises(BskyError, match=r"1 URI\(s\) are not records of the signed-in account did:plc:me, e\.g\. at://did:plc:other/"):
            acct.delete_many(["at://did:plc:me/app.bsky.feed.post/1", "at://did:plc:other/app.bsky.feed.post/2"])
        # only own records: past that check, stopping at the next missing piece (no client key)
        with pytest.raises(BskyError, match=r"no client key at .*: run keygen first"):
            acct.delete_many(["at://did:plc:me/app.bsky.feed.post/1"])

    def test_exit_code_contract(self):
        code = ("import { NothingSent, Usage, exitCode } from './errors.mjs'\n"
                "const cases = [['post', new Usage()], ['post', new NothingSent()], ['post', new Error()], ['delete', new Error()],\n"
                "  ['delete', new NothingSent()], ['whoami', new Error()], ['login', new Usage()], ['delete-many', new Error()]]\n"
                "console.log(JSON.stringify(cases.map(([c, e]) => exitCode(c, e))))\n")
        r = subprocess.run(["node", "--input-type=module", "-e", code], cwd=client.NODE_DIR, capture_output=True)
        assert r.returncode == 0, r.stderr
        # usage 2, nothing sent 3, a failed write (post, delete) 4, any other failure 1
        assert json.loads(r.stdout) == [2, 3, 4, 4, 3, 1, 2, 4]

    def test_delete_failures_stop_only_on_a_rate_limit(self):
        code = ("import { deleteFailure } from './errors.mjs'\n"
                "const e429 = Object.assign(new Error('Too Many Requests'), { status: 429 })\n"
                "console.log(JSON.stringify([deleteFailure(e429), deleteFailure(new Error('Rate Limit Exceeded')),\n"
                "  deleteFailure(new Error('Could not locate record')), deleteFailure('plain string')]))\n")
        r = subprocess.run(["node", "--input-type=module", "-e", code], cwd=client.NODE_DIR, capture_output=True)
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout) == [{"status": "rate_limited", "error": "Too Many Requests"},
                                        {"status": "rate_limited", "error": "Rate Limit Exceeded"},
                                        {"status": "failed", "error": "Could not locate record"},
                                        {"status": "failed", "error": "plain string"}]

    def test_official_rich_text_finds_the_link_and_every_hashtag(self):
        text = "Lattice Orbweaver (Araneus thaddeus)\nOctober 1, 2024 · Bucks County, PA\nhttps://www.inaturalist.org/observations/42\n#nature #Arachnida"
        code = ("import { RichText } from '@atproto/api'\n"
                "const rt = new RichText({ text: process.argv[1] }); rt.detectFacetsWithoutResolution()\n"
                "console.log(JSON.stringify(rt.facets.map((f) => [f.features[0].$type, f.index.byteStart, f.index.byteEnd])))\n")
        r = subprocess.run(["node", "--input-type=module", "-e", code, text], cwd=client.NODE_DIR, capture_output=True)
        assert r.returncode == 0, r.stderr
        raw = text.encode("utf-8")
        span = lambda s: [raw.index(s), raw.index(s) + len(s)]
        assert json.loads(r.stdout) == [["app.bsky.richtext.facet#link", *span(b"https://www.inaturalist.org/observations/42")],
                                        ["app.bsky.richtext.facet#tag", *span(b"#nature")],
                                        ["app.bsky.richtext.facet#tag", *span(b"#Arachnida")]]


class TestCli:
    def test_keygen_through_the_cli_and_an_unknown_profile_is_a_one_line_error(self, tmp_path, capsys):
        cfg = tmp_path / "p.toml"
        cfg.write_text(f"[profiles.poster]\nclient_id = 'https://e.github.io/c/client-metadata.json'\n"
                       f"redirect_uri = 'https://e.github.io/c/callback.html'\nscope = 'atproto'\nstate_dir = '{tmp_path / 'st'}'\n", encoding="utf-8")
        assert main(["--config", str(cfg), "--profile", "poster", "keygen"]) == 0
        assert json.loads(capsys.readouterr().out)["created"] == str(tmp_path / "st" / "client_key.jwk.json")
        assert main(["--config", str(cfg), "--profile", "nope", "keygen"]) == 1
        assert capsys.readouterr().err == f"bsky-oauth keygen: no profile 'nope' in {cfg} (have: poster)\n"
