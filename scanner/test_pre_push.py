"""Tests for pre_push.py through real pushes. Every hit string is built at runtime so this file stays clean."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pre_push

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "hooks"

AT = "@"
HOME_BOB = "/" + "home/bob"
NAME = "acme" + "-x"
BOB_MAIL = "bob" + AT + "corp.io"
CLEAN_MAIL = "t" + AT + "example.com"
SSH_GITHUB = "git" + AT + "github.com:o/r.git"
NAMES_ENV = "REPO_GUARDS_PRIVATE_NAMES"
FAKE_GH = '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_GH_LOG"\nprintf "%s\\n" "$FAKE_GH_OUT"\nexit "${FAKE_GH_EXIT:-0}"\n'
LIST_HINT = "private-name list is not set up"
REWRITE = "amend or rebase"


class Push:
    """A clone whose pre-push hook is this repo's, pushing to a local bare remote (not GitHub, so public)."""

    def __init__(self, test: unittest.TestCase):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp, True)
        self.remote = tmp / "remote.git"
        self.dir = tmp / "work"
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH)
        (bin_dir / "gh").chmod(0o755)
        self.gh_log = tmp / "gh.log"
        self.env = {k: v for k, v in os.environ.items() if k != NAMES_ENV and not k.startswith("GIT_")}
        self.env.update(
            HOME=str(tmp),
            PATH="%s%s%s" % (bin_dir, os.pathsep, os.environ.get("PATH", "")),
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL=CLEAN_MAIL,
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL=CLEAN_MAIL,
            FAKE_GH_LOG=str(self.gh_log),
            FAKE_GH_OUT="",
        )
        self.env[NAMES_ENV] = NAME
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], env=self.env, check=True)
        self.dir.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.hooksPath", str(HOOKS))
        self.git("remote", "add", "origin", str(self.remote))

    def git(self, *args: str, env: dict[str, str] | None = None) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.dir, env=env or self.env, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def commit(self, files: dict[str, str | bytes] | None = None, message: str = "change", **env: str) -> str:
        for name, content in (files or {}).items():
            path = self.dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, env={**self.env, **env})
        return self.git("rev-parse", "HEAD")

    def push(self, *refspecs: str, hook: bool = True) -> subprocess.CompletedProcess[str]:
        config = [] if hook else ["-c", "core.hooksPath=" + os.devnull]
        return subprocess.run(
            ["git", *config, "push", "-q", "origin", *(refspecs or ("main",))],
            cwd=self.dir, env=self.env, capture_output=True, text=True,
        )

    def hook(self, url: str, updates: str) -> subprocess.CompletedProcess[str]:
        """Run the hook as git would for a push to `url`, with `updates` as its stdin."""
        return subprocess.run(
            [str(HOOKS / "pre-push"), "origin", url], input=updates, cwd=self.dir, env=self.env,
            capture_output=True, text=True,
        )

    def on_remote(self, ref: str = "refs/heads/main") -> bool:
        listed = subprocess.run(["git", "ls-remote", str(self.remote), ref], env=self.env,
                                capture_output=True, text=True, check=True).stdout
        return bool(listed.strip())


class PublicPush(unittest.TestCase):
    def setUp(self):
        self.p = Push(self)

    def assertRefused(self, result, *needles: str):
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("pre-push: refused", result.stderr)
        for needle in needles:
            self.assertIn(needle, result.stderr)
        self.assertFalse(self.p.on_remote())

    def assertPushed(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(self.p.on_remote())

    def test_clean_commit_is_pushed(self):
        self.p.commit({"README.md": "clean\n"})
        self.assertPushed(self.p.push())

    def test_tracker_id_in_message_is_pushed(self):
        self.p.commit({"README.md": "clean\n"}, message="ABC-123: fix the thing")
        self.assertPushed(self.p.push())

    def test_home_path_in_file_is_refused_without_its_text(self):
        sha = self.p.commit({"a.txt": "clean\n", "notes.txt": "built in %s/src\n" % HOME_BOB})
        result = self.p.push()
        self.assertRefused(result, "file #2:1:10: local-path (leak)", "file #2 is entry 2 of commit %s" % sha)
        self.assertNotIn(HOME_BOB, result.stderr)
        self.assertNotIn("notes.txt", result.stderr)
        self.assertIn(REWRITE, result.stderr)
        self.assertNotIn("already public", result.stderr)

    def test_home_path_in_binary_blob_is_refused(self):
        self.p.commit({"x.bin": b"\x00\x01" + HOME_BOB.encode() + b"/x\x00"})
        self.assertRefused(self.p.push(), "local-path (leak)")

    def test_junk_path_is_refused(self):
        self.p.commit({"pkg/__pycache__/m.cpython-311.pyc": b"\x00", "node_modules/a/index.js": "x\n"})
        result = self.p.push()
        self.assertRefused(result, "file #1 path: junk (leak)", "file #2 path: junk (leak)")
        self.assertNotIn("__pycache__", result.stderr)

    def test_email_in_author_is_refused(self):
        sha = self.p.commit({"a.txt": "clean\n"}, GIT_AUTHOR_EMAIL=BOB_MAIL)
        result = self.p.push()
        self.assertRefused(result, "commit %s author: email (leak)" % sha)
        self.assertNotIn(BOB_MAIL, result.stderr)

    def test_private_name_in_author_name_is_refused(self):
        sha = self.p.commit({"a.txt": "clean\n"}, GIT_AUTHOR_NAME=NAME)
        self.assertRefused(self.p.push(), "commit %s author name: private-name (leak)" % sha)

    def test_private_name_in_message_is_refused(self):
        sha = self.p.commit({"a.txt": "clean\n"}, message="for %s" % NAME.upper())
        self.assertRefused(self.p.push(), "commit %s line 1: private-name (leak)" % sha)

    def test_private_name_in_branch_name_is_refused(self):
        self.p.commit({"a.txt": "clean\n"})
        result = self.p.push("main:refs/heads/for-" + NAME)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pushed ref #1 line 1: private-name (leak)", result.stderr)
        self.assertNotIn(NAME, result.stderr)

    def test_annotated_tag_message_is_refused(self):
        self.p.commit({"a.txt": "clean\n"})
        self.assertPushed(self.p.push())
        self.p.git("tag", "-a", "v1", "-m", "see %s" % HOME_BOB)
        tag = self.p.git("rev-parse", "v1")
        result = self.p.push("v1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tag %s message line 1: local-path (leak)" % tag, result.stderr)
        self.assertFalse(self.p.on_remote("refs/tags/v1"))

    def test_commit_already_on_remote_is_not_rescanned(self):
        self.p.commit({"notes.txt": HOME_BOB + "\n"})
        self.assertEqual(self.p.push(hook=False).returncode, 0)
        self.p.commit({"a.txt": "clean\n"})
        self.assertEqual(self.p.push().returncode, 0, self.p.push().stderr)

    def test_hit_in_an_earlier_pushed_commit_is_refused_after_removal(self):
        self.p.commit({"notes.txt": HOME_BOB + "\n"})
        self.p.git("rm", "-q", "notes.txt")
        self.p.commit(message="remove it")
        self.assertRefused(self.p.push(), "local-path (leak)")

    def test_merge_scans_both_sides(self):
        self.p.commit({"a.txt": "clean\n"})
        self.assertPushed(self.p.push())
        self.p.git("checkout", "-q", "-b", "side")
        self.p.commit({"b.txt": HOME_BOB + "\n"})
        self.p.git("checkout", "-q", "main")
        self.p.commit({"c.txt": "clean\n"})
        self.p.git("merge", "-q", "--no-edit", "side")
        result = self.p.push()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local-path (leak)", result.stderr)

    def test_delete_only_push_succeeds(self):
        self.p.commit({"a.txt": "clean\n"})
        self.assertPushed(self.p.push("main", "main:refs/heads/old"))
        result = self.p.push(":refs/heads/old")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.p.on_remote("refs/heads/old"))

    def test_missing_list_is_refused_with_its_cause(self):
        del self.p.env[NAMES_ENV]
        self.p.commit({"a.txt": "clean\n"})
        result = self.p.push()
        self.assertRefused(result, LIST_HINT, NAMES_ENV, "hooks/README.md")
        self.assertNotIn("has hits", result.stderr)
        self.assertNotIn(REWRITE, result.stderr)
        self.assertNotIn("rewrite", result.stderr)

    def test_missing_list_and_hits_give_both_causes(self):
        del self.p.env[NAMES_ENV]
        self.p.commit({"notes.txt": HOME_BOB + "\n"})
        self.assertRefused(self.p.push(), LIST_HINT, REWRITE, "local-path (leak)")

    def test_ref_naming_a_blob_is_refused_as_a_scan_failure(self):
        self.p.commit({"a.txt": "clean\n"})
        blob = self.p.git("rev-parse", "HEAD:a.txt")
        result = self.p.push(blob + ":refs/tags/blob")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pre-push: refused: the leak scan failed", result.stderr)
        self.assertIn("blob", result.stderr)
        self.assertFalse(self.p.on_remote("refs/tags/blob"))

    def test_bad_hook_arguments_are_refused(self):
        result = subprocess.run([str(HOOKS / "pre-push")], input="", cwd=self.p.dir, env=self.p.env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the leak scan failed (IndexError)", result.stderr)


class PrivatePush(unittest.TestCase):
    """GitHub URLs reach the fake `gh`; a private answer skips the scan, so nothing contacts the URL."""

    def setUp(self):
        self.p = Push(self)
        sha = self.p.commit({"notes.txt": HOME_BOB + "\n"}, message="ABC-123: home path")
        self.updates = "refs/heads/main %s refs/heads/main %s\n" % (sha, "0" * 40)

    def test_private_repo_with_home_path_and_tracker_id_succeeds(self):
        for visibility in ("private", "internal"):
            self.p.env["FAKE_GH_OUT"] = visibility
            result = self.p.hook("https://github.com/o/r.git", self.updates)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
        self.assertIn("api --hostname github.com repos/o/r --jq .visibility", self.p.gh_log.read_text())

    def test_private_repo_needs_no_list(self):
        del self.p.env[NAMES_ENV]
        self.p.env["FAKE_GH_OUT"] = "private"
        self.assertEqual(self.p.hook(SSH_GITHUB, self.updates).returncode, 0)


class Visibility(unittest.TestCase):
    def setUp(self):
        p = Push(self)
        self.log = p.gh_log
        patcher = mock.patch.dict(os.environ, {k: p.env[k] for k in ("PATH", "FAKE_GH_LOG")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def answer(self, out: str, code: int = 0):
        return mock.patch.dict(os.environ, {"FAKE_GH_OUT": out, "FAKE_GH_EXIT": str(code)})

    def test_github_url_forms(self):
        urls = ("https://github.com/o/r.git", "https://github.com/o/r", "https://tok" + AT + "github.com/o/r/",
                SSH_GITHUB, "ssh://git" + AT + "github.com/o/r.git", "ssh://git" + AT + "github.com:22/o/r")
        with self.answer("private"):
            for url in urls:
                self.assertFalse(pre_push.is_public(url), url)
        self.assertEqual(self.log.read_text().count("repos/o/r "), len(urls))

    def test_only_private_or_internal_skips(self):
        for out, code in (("public", 0), ("", 0), ("PRIVATE", 0), ("private", 1)):
            with self.answer(out, code):
                self.assertTrue(pre_push.is_public("https://github.com/o/r"), (out, code))

    def test_other_hosts_and_odd_paths_are_public_without_asking(self):
        for url in ("https://gitlab.com/o/r.git", "/srv/r.git", "file:///srv/r.git", "https://github.com/o/r/x",
                    "https://github.com/o/r?x=1", "https://github.com.evil.io/o/r", ""):
            self.assertTrue(pre_push.is_public(url), url)
        self.assertFalse(self.log.exists())

    def test_missing_gh_is_public(self):
        with mock.patch.dict(os.environ, {"PATH": os.devnull}):
            self.assertTrue(pre_push.is_public("https://github.com/o/r"))


if __name__ == "__main__":
    unittest.main()
