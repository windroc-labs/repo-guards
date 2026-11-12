"""Tests for scan.py. Every hit string is built at runtime so this file stays clean (FR-20)."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "scan.py"
REPO_ROOT = SCRIPT.parent.parent

EM = chr(0x2014)
NAME = "acme" + "-x"
HOME_BOB = "/" + "home/bob"
USERS_BOB = "/" + "Users/bob"
WIN_BOB = "C:" + "\\" + "Users" + "\\" + "bob"
AT = "@"
BOB_MAIL = "bob" + AT + "corp.io"
CLEAN_MAIL = "t" + AT + "example.com"
SECRET_ENV = "REPO_GUARDS_PRIVATE_NAMES"


def git_env(email=CLEAN_MAIL):
    env = {k: v for k, v in os.environ.items() if k != SECRET_ENV and not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL=email,
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL=CLEAN_MAIL,
    )
    return env


class Repo:
    """A temporary git repo with an `origin/main` ref at its first commit."""

    def __init__(self, test, files=None):
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir = self.tmp / "repo"
        self.outside = self.tmp / "outside"
        self.dir.mkdir()
        self.outside.mkdir()
        self.git("init", "-q", "-b", "main")
        self.write(files or {"README.md": "clean\n"})
        self.commit("init")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def git(self, *args, env=None):
        return subprocess.run(
            ["git", *args], cwd=self.dir, env=env or git_env(), check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def write(self, files):
        for name, content in files.items():
            path = self.dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")

    def commit(self, message, email=CLEAN_MAIL):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, env=git_env(email))
        return self.git("rev-parse", "HEAD")

    def names_file(self, *entries, raw=None):
        path = self.outside / "names.txt"
        path.write_bytes(raw if raw is not None else "".join(e + "\n" for e in entries).encode())
        return str(path)

    def index(self, name):
        return self.git("ls-files").splitlines().index(name) + 1

    def scan(self, *args, env=None):
        run_env = git_env()
        run_env.update(env or {})
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=self.dir, env=run_env,
            capture_output=True, text=True,
        )
        return proc.returncode, proc.stdout + proc.stderr


def summary_count(out, check):
    match = re.search(r"^\s*%s\s+(leak|hygiene)\s+(\S+)" % re.escape(check), out, re.M)
    return match and (match.group(1), match.group(2))


PUBLIC = "--checks", "local-path,email,em-dash"


class FileContent(unittest.TestCase):
    def test_s1_hit_in_an_unchanged_file_is_found(self):
        repo = Repo(self, {"docs/a.md": "one\ntwo\nthree " + EM + "\n", "docs/b.md": "b\n"})
        repo.write({"docs/b.md": "b2\n"})
        repo.commit("change b only")
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 1, out)
        self.assertIn("docs/a.md:3", out)
        self.assertIn("em-dash", out)

    def test_s11_chinese_double_em_dash(self):
        repo = Repo(self, {"a.md": "甲" + EM * 2 + "乙\n"})
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 1, out)
        self.assertEqual(summary_count(out, "em-dash"), ("hygiene", "2"))

    def test_s8_home_paths(self):
        for path, fails in (
            (USERS_BOB + "/x", True),
            (HOME_BOB + "/x", True),
            (WIN_BOB + "\\x", True),
            ("/" + "Users/<user>/x", False),
        ):
            with self.subTest(path=path):
                repo = Repo(self, {"a.md": path + "\n"})
                code, out = repo.scan(*PUBLIC)
                self.assertEqual(code, 1 if fails else 0, out)
                self.assertNotIn(path, out)
                self.assertNotIn("bob", out)

    def test_local_path_inside_a_url_is_not_a_hit(self):
        repo = Repo(self, {"a.md": "https://example.com" + HOME_BOB + "\n"})
        self.assertEqual(repo.scan(*PUBLIC)[0], 0)

    def test_s9_email_allowlist(self):
        for email, fails in (
            (BOB_MAIL, True),
            ("noreply" + AT + "corp.io", True),
            ("1+bot" + AT + "users.noreply.github.com", False),
            ("noreply" + AT + "anthropic.com", False),
            ("noreply" + AT + "github.com", False),
            (CLEAN_MAIL, False),
            ("a" + AT + "host.test", False),
            ("a" + AT + "mail.example.org", False),
            ("a" + AT + "Example.NET", False),
            ("a" + AT + "corp.example", False),
            ("a" + AT + "notexample.com", True),
            ("uses: actions/checkout" + AT + "v4.1.0", False),
        ):
            with self.subTest(email=email):
                repo = Repo(self, {"a.md": email + "\n"})
                code, out = repo.scan(*PUBLIC)
                self.assertEqual(code, 1 if fails else 0, out)
                self.assertNotIn(email, out)

    def test_s14_inline_exemptions_have_no_effect(self):
        for comment in ("<!-- allow -->", "# noqa", "<!-- repo-guards:allow -->"):
            with self.subTest(comment=comment):
                repo = Repo(self, {"a.md": HOME_BOB + " " + comment + "\n"})
                self.assertEqual(repo.scan(*PUBLIC)[0], 1)

    def test_s18_categories_positions_and_summary(self):
        repo = Repo(self, {"a.md": "x " + EM + " y " + EM + "\n" + HOME_BOB + "\n", "b.md": "b\n"})
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 1, out)
        self.assertIn("a.md:1:3: em-dash", out)
        self.assertIn("a.md:1:7: em-dash", out)
        self.assertIn("file #%d:2:1: local-path" % repo.index("a.md"), out)
        self.assertNotRegex(out, r"a\.md:2")
        self.assertEqual(summary_count(out, "em-dash"), ("hygiene", "2"))
        self.assertEqual(summary_count(out, "local-path"), ("leak", "1"))
        self.assertEqual(summary_count(out, "email"), ("leak", "0"))
        self.assertIn("If something leaks", out)
        self.assertIn("files with hits: 1, total hits: 3", out)

    def test_no_leak_notice_for_hygiene_hits_only(self):
        repo = Repo(self, {"a.md": EM + "\n"})
        self.assertNotIn("If something leaks", repo.scan(*PUBLIC)[1])

    def test_public_output_has_counts_only(self):
        repo = Repo(self, {"a.md": EM + "\n" + HOME_BOB + "\n" + BOB_MAIL + "\n"})
        code, out = repo.scan(*PUBLIC, "--public")
        self.assertEqual(code, 1, out)
        self.assertNotIn("a.md", out)
        self.assertNotIn("file #", out)
        self.assertNotIn("bob", out)
        self.assertEqual(summary_count(out, "em-dash"), ("hygiene", "1"))
        self.assertEqual(summary_count(out, "local-path"), ("leak", "1"))
        self.assertEqual(summary_count(out, "email"), ("leak", "1"))
        self.assertIn("files with hits: 1, total hits: 3", out)
        self.assertIn("If something leaks", out)

    def test_clean_repo_passes(self):
        repo = Repo(self)
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 0, out)
        self.assertIn("files with hits: 0, total hits: 0", out)

    def test_fr1_non_text_files_are_skipped_and_counted(self):
        repo = Repo(self, {"a.bin": b"\xff\xfe" + EM.encode() + b"\n", "a.md": "ok\n"})
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 0, out)
        self.assertIn("skipped non-text files: 1", out)

    def test_untracked_files_are_not_scanned(self):
        repo = Repo(self)
        repo.write({"loose.md": EM + "\n"})
        self.assertEqual(repo.scan(*PUBLIC)[0], 0)

    def test_unknown_check_is_a_usage_error(self):
        self.assertEqual(Repo(self).scan("--checks", "nope")[0], 2)


class PrivateNames(unittest.TestCase):
    def scan_names(self, text, *entries):
        repo = Repo(self, {"notes.md": text + "\n"})
        return repo.scan("--checks", "private-name", "--names-file", repo.names_file(*entries))

    def test_s5_match_rules_and_log_never_shows_the_entry(self):
        for text, fails in (("see ACME-X repo", True), (NAME + "y", False)):
            with self.subTest(text=text):
                code, out = self.scan_names(text, NAME)
                self.assertEqual(code, 1 if fails else 0, out)
                self.assertNotIn(NAME, out.lower())

    def test_boundaries(self):
        for text, fails in (
            ("x" + NAME, False),
            ("1" + NAME, False),
            ("(" + NAME + ")", True),
            ("_" + NAME, True),
            ("看" + NAME + "的", True),
            (NAME + "-y", True),
            ("b" + "a-a-a", True),
        ):
            with self.subTest(text=text):
                entry = "a-a" if text.startswith("ba") else NAME
                self.assertEqual(self.scan_names(text, entry)[0], 1 if fails else 0)

    def test_name_inside_chinese_prose_hits(self):
        self.assertEqual(self.scan_names("这是张三的", "张三")[0], 1)

    def test_entries_are_trimmed_and_blank_lines_ignored(self):
        self.assertEqual(self.scan_names("hi " + NAME, "", "  " + NAME + "  ", "")[0], 1)

    def test_list_from_environment(self):
        repo = Repo(self, {"a.md": NAME + "\n"})
        code, out = repo.scan("--checks", "private-name", env={SECRET_ENV: "other\n" + NAME + "\n"})
        self.assertEqual(code, 1, out)

    def test_s6_empty_list_fails_and_other_checks_still_report(self):
        repo = Repo(self, {"a.md": "a " + EM + "\n"})
        code, out = repo.scan("--checks", "private-name,em-dash", env={SECRET_ENV: ""})
        self.assertEqual(code, 1, out)
        self.assertIn("名单不可用", out)
        self.assertEqual(summary_count(out, "em-dash"), ("hygiene", "1"))
        self.assertEqual(summary_count(out, "private-name"), ("leak", "unavailable"))

    def test_fr8_unparsable_or_missing_list_is_unavailable(self):
        repo = Repo(self, {"a.md": "a\n"})
        for args in (
            ("--names-file", repo.names_file(raw=b"\xff\xfe\n")),
            ("--names-file", repo.names_file(raw=b" \n\n")),
            ("--names-file", str(repo.outside / "missing.txt")),
            (),
        ):
            with self.subTest(args=args):
                code, out = repo.scan("--checks", "private-name", *args)
                self.assertEqual(code, 1, out)
                self.assertIn("名单不可用", out)

    def test_s31_local_run(self):
        repo = Repo(self, {"a.md": NAME + "\n"})
        code, out = repo.scan("--names-file", repo.names_file(NAME))
        self.assertEqual(code, 1, out)
        self.assertIn("private-name", out)
        code, out = repo.scan()
        self.assertEqual(code, 1, out)
        self.assertIn("名单不可用", out)

    def test_s35_list_inside_the_work_tree_is_refused(self):
        repo = Repo(self, {"a.md": EM + "\n"})
        inside = repo.dir / "names.txt"
        inside.write_text(NAME + "\n")
        for path in (str(inside), "names.txt", "sub/../names.txt"):
            with self.subTest(path=path):
                code, out = repo.scan("--names-file", path)
                self.assertEqual(code, 2, out)
                self.assertNotIn("em-dash", out)
                self.assertNotIn("total hits", out)

    def test_s30_public_checks_never_read_the_list(self):
        repo = Repo(self, {"a.md": NAME + "\n"})
        inside = repo.dir / "names.txt"
        inside.write_text(NAME + "\n")
        code, out = repo.scan(*PUBLIC, "--names-file", str(inside), env={SECRET_ENV: NAME})
        self.assertEqual(code, 0, out)
        self.assertNotIn("private-name", out)

    def test_s33_path_hits_and_symlinks(self):
        target = NAME + "-notes.md"
        repo = Repo(self, {target: "see " + HOME_BOB + "\n"})
        secret = repo.outside / "env"
        secret.write_text(EM + " " + BOB_MAIL + "\n")
        os.symlink("../outside/env", repo.dir / "env.md")
        os.symlink("/proc/self/environ", repo.dir / "proc.md")
        repo.commit("add")
        index = repo.index(target)

        code, out = repo.scan("--checks", "private-name", "--names-file", repo.names_file(NAME))
        self.assertEqual(code, 1, out)
        self.assertNotIn(NAME, out.lower())
        self.assertIn("file #%d path: private-name" % index, out)

        code, out = repo.scan(*PUBLIC, env={"LEAK_PROBE": BOB_MAIL})
        self.assertEqual(code, 1, out)
        self.assertIn("file #%d:1:5: local-path" % index, out)
        self.assertNotIn(NAME, out.lower())
        self.assertEqual(summary_count(out, "email"), ("leak", "0"))
        self.assertEqual(summary_count(out, "em-dash"), ("hygiene", "0"))
        self.assertEqual(summary_count(out, "local-path"), ("leak", "1"))

    def test_symlink_target_string_is_scanned(self):
        repo = Repo(self)
        os.symlink(HOME_BOB + "/x", repo.dir / "link.md")
        repo.commit("link")
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 1, out)
        self.assertIn("file #%d:1:1: local-path" % repo.index("link.md"), out)

    def test_fr39_path_hits_for_local_path_and_email(self):
        repo = Repo(self, {"docs/" + BOB_MAIL + ".md": "x\n"})
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 1, out)
        self.assertIn("file #%d path: email" % repo.index("docs/" + BOB_MAIL + ".md"), out)


class Commits(unittest.TestCase):
    def test_s2_commit_message_hit(self):
        repo = Repo(self)
        sha = repo.commit("first " + EM + " line\n\nbody")
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s line 1: em-dash" % sha, out)

    def test_s4_push_range_reports_the_new_head(self):
        repo = Repo(self)
        before = repo.git("rev-parse", "HEAD")
        sha = repo.commit("squash " + EM)
        code, out = repo.scan(*PUBLIC, "--commits", before + ".." + sha)
        self.assertEqual(code, 1, out)
        self.assertIn(sha, out)

    def test_commits_outside_the_range_are_not_scanned(self):
        repo = Repo(self)
        repo.commit("old " + EM)
        repo.git("update-ref", "refs/remotes/origin/main", "HEAD")
        repo.commit("clean")
        self.assertEqual(repo.scan(*PUBLIC)[0], 0)

    def test_s10_author_email(self):
        repo = Repo(self)
        sha = repo.commit("clean", email=BOB_MAIL)
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s author: email" % sha, out)
        self.assertNotIn(BOB_MAIL, out)

    def test_fr13_committer_email(self):
        repo = Repo(self)
        env = git_env()
        env["GIT_COMMITTER_EMAIL"] = BOB_MAIL
        repo.git("commit", "-q", "--allow-empty", "-m", "clean", env=env)
        sha = repo.git("rev-parse", "HEAD")
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s committer: email" % sha, out)

    def test_allowed_commit_emails_pass(self):
        repo = Repo(self)
        repo.commit("clean", email="1+bot" + AT + "users.noreply.github.com")
        self.assertEqual(repo.scan(*PUBLIC)[0], 0)

    def test_s34_local_run_checks_unpushed_commits(self):
        repo = Repo(self)
        sha = repo.commit("see " + HOME_BOB, email=BOB_MAIL)
        code, out = repo.scan("--names-file", repo.names_file(NAME))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s author: email" % sha, out)
        self.assertIn("commit %s line 1: local-path" % sha, out)

    def test_private_names_in_commit_messages(self):
        repo = Repo(self)
        sha = repo.commit("x\n\nabout " + NAME)
        code, out = repo.scan("--checks", "private-name", "--names-file", repo.names_file(NAME))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s line 3: private-name" % sha, out)
        self.assertNotIn(NAME, out.lower())

    def test_bad_range_is_a_usage_error(self):
        repo = Repo(self)
        code, out = repo.scan(*PUBLIC, "--commits", "nope..HEAD")
        self.assertEqual(code, 2, out)
        self.assertNotIn("total hits", out)

    def test_public_output_hides_shas(self):
        repo = Repo(self)
        sha = repo.commit("x " + EM, email=BOB_MAIL)
        code, out = repo.scan(*PUBLIC, "--public")
        self.assertEqual(code, 1, out)
        self.assertNotIn(sha, out)
        self.assertIn("total hits: 2", out)


class PullRequest(unittest.TestCase):
    def test_s3_body_line(self):
        repo = Repo(self)
        body = "first\n" + "/" + "Users/someone/x\n"
        code, out = repo.scan(*PUBLIC, "--pr-from-env", env={"PR_TITLE": "t", "PR_BODY": body})
        self.assertEqual(code, 1, out)
        self.assertIn("body line 2: local-path", out)
        self.assertNotIn("someone", out)

    def test_s29_title_private_name(self):
        repo = Repo(self)
        code, out = repo.scan(
            "--checks", "private-name", "--pr-from-env", "--names-file", repo.names_file(NAME),
            env={"PR_TITLE": "fix " + NAME, "PR_BODY": ""},
        )
        self.assertEqual(code, 1, out)
        self.assertIn("title line 1: private-name", out)
        self.assertNotIn(NAME, out.lower())

    def test_s28_shell_text_in_the_title_is_data(self):
        repo = Repo(self, {"a.md": NAME + "\n"})
        code, out = repo.scan(
            "--checks", "private-name", "--pr-from-env", "--names-file", repo.names_file(NAME),
            env={"PR_TITLE": '"; exit 0; #', "PR_BODY": "$(true)"},
        )
        self.assertEqual(code, 1, out)

    def test_pr_fields_not_read_without_the_flag(self):
        repo = Repo(self)
        code, out = repo.scan(*PUBLIC, env={"PR_TITLE": EM, "PR_BODY": EM})
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
