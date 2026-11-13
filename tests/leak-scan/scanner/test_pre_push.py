"""Tests for hooks/pre-push through real pushes. Every hit string is built at runtime so this file stays clean."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[3] / "guards/leak-scan/hooks"

AT = "@"
HOME_BOB = "/" + "home/bob"
NAME = "acme" + "-x"
BOB_MAIL = "bob" + AT + "corp.io"
CLEAN_MAIL = "t" + AT + "example.com"
NAMES_ENV = "REPO_GUARDS_PRIVATE_NAMES"


class Push:
    """A clone whose pre-push hook is this repo's, pushing to a local bare remote."""

    def __init__(self, test: unittest.TestCase):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp, True)
        self.remote = tmp / "remote.git"
        self.dir = tmp / "work"
        self.env = {k: v for k, v in os.environ.items() if k != NAMES_ENV and not k.startswith("GIT_")}
        self.env.update(
            HOME=str(tmp),
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL=CLEAN_MAIL,
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL=CLEAN_MAIL,
        )
        self.env[NAMES_ENV] = NAME
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], env=self.env, check=True)
        self.dir.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.hooksPath", str(HOOKS))
        self.git("remote", "add", "origin", str(self.remote))

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.dir, env=self.env, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def commit(self, files: dict[str, str], message: str = "change") -> str:
        for name, content in files.items():
            (self.dir / name).write_text(content, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def push(self, *refspecs: str, hook: bool = True) -> subprocess.CompletedProcess[str]:
        config = [] if hook else ["-c", "core.hooksPath=" + os.devnull]
        return subprocess.run(
            ["git", *config, "push", "-q", "origin", *(refspecs or ("main",))],
            cwd=self.dir, env=self.env, capture_output=True, text=True,
        )

    def remote_tip(self, ref: str = "refs/heads/main") -> str:
        listed = subprocess.run(["git", "ls-remote", str(self.remote), ref], env=self.env,
                                capture_output=True, text=True, check=True).stdout
        return listed.split("\t")[0]


class PrePushHook(unittest.TestCase):
    def setUp(self):
        self.p = Push(self)

    def assertRefused(self, result, *needles: str, tip: str = ""):
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        for needle in needles:
            self.assertIn(needle, output)
        self.assertEqual(self.p.remote_tip(), tip, "the remote must not change")

    def assertPushed(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.p.remote_tip(), self.p.git("rev-parse", "HEAD"))

    def test_clean_commit_with_a_tracker_id_is_pushed(self):
        self.p.commit({"README.md": "clean\n"}, message="ABC-123: fix the thing")
        self.assertPushed(self.p.push())

    def test_home_path_in_a_file_is_refused_without_its_text(self):
        sha = self.p.commit({"a.txt": "clean\n", "notes.txt": "built in %s/src\n" % HOME_BOB})
        result = self.p.push()
        self.assertRefused(result, "commit %s file #2:1:10: local-path (leak)" % sha, "Nothing was pushed")
        self.assertNotIn(HOME_BOB, result.stdout + result.stderr)
        self.assertNotIn("notes.txt", result.stdout + result.stderr)

    def test_author_email_and_private_name_in_the_message_are_refused(self):
        self.p.git("commit", "-q", "--allow-empty", "-m", "about " + NAME, "--author", "t <%s>" % BOB_MAIL)
        result = self.p.push()
        self.assertRefused(result, "author: email", "line 1: private-name")
        self.assertNotIn(NAME, (result.stdout + result.stderr).lower())

    def test_merge_brings_in_a_side_commit_with_a_hit(self):
        self.p.commit({"README.md": "clean\n"})
        self.assertPushed(self.p.push())
        base = self.p.git("rev-parse", "HEAD")
        self.p.git("checkout", "-q", "-b", "side")
        side = self.p.commit({"side.txt": "about %s\n" % NAME})
        self.p.git("checkout", "-q", "main")
        self.p.commit({"main.txt": "clean\n"})
        self.p.git("merge", "-q", "--no-edit", "side")
        self.assertRefused(self.p.push(), "commit %s file #1:1:7: private-name" % side, tip=base)

    def test_what_the_remote_already_has_is_not_rescanned(self):
        self.p.commit({"old.txt": "see %s\n" % HOME_BOB})
        self.assertPushed(self.p.push(hook=False))
        self.p.commit({"new.txt": "clean\n"})
        self.assertPushed(self.p.push())

    def test_missing_list_is_refused(self):
        del self.p.env[NAMES_ENV]
        self.p.commit({"README.md": "clean\n"})
        self.assertRefused(self.p.push(), "private-name list unavailable")


if __name__ == "__main__":
    unittest.main()
