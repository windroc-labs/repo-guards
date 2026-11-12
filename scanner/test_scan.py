"""Tests for scan.py. Every hit string is built at runtime so this file stays clean (FR-20)."""

import json
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

    def scan(self, *args, env=None, stdin=""):
        run_env = git_env()
        run_env.update(env or {})
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=self.dir, env=run_env,
            input=stdin, capture_output=True, text=True,
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

    def test_local_path_forms_that_must_hit(self):
        for text in (
            "路径是" + HOME_BOB + "/x",
            "-I" + HOME_BOB + "/include",
            "C:" + "\\\\" + "Users" + "\\\\" + "bob",
            "c:" + "\\" + "users" + "\\" + "bob",
            "file://" + USERS_BOB + "/x",
            "PATH=http://proxy:8080," + HOME_BOB + "/bin",
            "url=https://a.b;dir=" + HOME_BOB + "/x",
            "~" + HOME_BOB,
            "CFLAGS=-I" + HOME_BOB + "/include",
            'args: ["-I' + HOME_BOB + '/include"]',
            "`-I" + HOME_BOB + "/include`",
            "LDFLAGS='-L" + HOME_BOB + "/lib'",
            "_" + HOME_BOB + "/x_",
            'DATA_DIR="${DATA_DIR:-' + HOME_BOB + '/data}"',
            "X=${DIR-" + HOME_BOB + "}",
        ):
            with self.subTest(text=text):
                repo = Repo(self, {"a.md": text + "\n"})
                self.assertEqual(repo.scan(*PUBLIC)[0], 1)

    def test_home_segments_anywhere_hit(self):
        for text in (
            "https://example.com" + HOME_BOB,
            "src" + HOME_BOB + "/page.tsx",
            "GET /api/v1" + USERS_BOB,
            "app" + USERS_BOB + "/Profile.cs",
            "." + HOME_BOB,
            "https://h.com/a/-v" + HOME_BOB,
            "name-" + HOME_BOB,
            "ssh://devbox" + HOME_BOB + "/repo",
            "https://[::1]" + HOME_BOB + "/x",
            "a${X=" + HOME_BOB + "}",
            "x${X:-C:" + "\\" + "Users" + "\\" + "bob}",
            "xC:" + "\\" + "Users" + "\\" + "bob",
            "cd /" + 'home/"bob"/proj',
            "/" + "home/'bob'",
            "/" + "home/`bob`",
            "/" + "home/<bob/x",
            "/" + "home/<user>bob",
            "/" + "home//bob",
            "C:" + "\\" * 3 + "Users" + "\\" + "bob",
            "/" + "home/<user>.name",
            "/" + "home/<my user>",
            "/" + "home/<user>-bob",
            "cd /" + "home/<bob && echo a > out",
            "/" + "home/<your name>/x",
            "/" + "home/x<user>",
            "/" + "home/<user>,bob",
        ):
            with self.subTest(text=text):
                repo = Repo(self, {"a.md": text + "\n"})
                self.assertEqual(repo.scan(*PUBLIC)[0], 1)

    def test_tracked_path_with_a_home_segment_hits(self):
        repo = Repo(self, {"web" + HOME_BOB + ".md": "x\n"})
        code, out = repo.scan(*PUBLIC)
        self.assertEqual(code, 1, out)
        self.assertIn("file #%d path: local-path" % repo.index("web" + HOME_BOB + ".md"), out)

    def test_placeholder_names_never_hit(self):
        for text in ("src/home/<user>/x", "https://host/Users/<name>", "-I/home/<user>", "`/Users/<user>`", "(/home/<user_1>)", "under /" + "home/<user>.", "/Users/<name>, /home/<name> or", "/home/<user-name>", "C:" + "\\" + "Users" + "\\" + "<user>"):
            with self.subTest(text=text):
                repo = Repo(self, {"a.md": text + "\n"})
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

    def test_content_hit_is_reported_by_file_index(self):
        repo = Repo(self, {"a.md": "see " + NAME + "\n"})
        code, out = repo.scan("--checks", "private-name", "--names-file", repo.names_file(NAME))
        self.assertEqual(code, 1, out)
        self.assertIn("file #%d:1:5: private-name (leak)" % repo.index("a.md"), out)
        self.assertNotIn("a.md", out)

    def test_list_with_a_byte_order_mark(self):
        repo = Repo(self, {"a.md": "see " + NAME + "\n"})
        names = repo.names_file(raw=b"\xef\xbb\xbf" + NAME.encode() + b"\n")
        self.assertEqual(repo.scan("--checks", "private-name", "--names-file", names)[0], 1)

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

    def test_public_output_hides_pr_fields_and_commits(self):
        repo = Repo(self)
        sha = repo.commit("x " + EM)
        code, out = repo.scan(
            "--names-file", repo.names_file(NAME), "--pr-from-env", "--public",
            env={"PR_TITLE": NAME, "PR_BODY": "a\n" + HOME_BOB},
        )
        self.assertEqual(code, 1, out)
        for hidden in (sha, "title", "body", "line", NAME, "bob"):
            self.assertNotIn(hidden, out)
        self.assertIn("total hits: 3", out)

    def test_pr_fields_not_read_without_the_flag(self):
        repo = Repo(self)
        code, out = repo.scan(*PUBLIC, env={"PR_TITLE": EM, "PR_BODY": EM})
        self.assertEqual(code, 0, out)

    def event_file(self, repo, pull_request):
        path = repo.outside / "event.json"
        path.write_text(json.dumps({"action": "opened", "pull_request": pull_request}), encoding="utf-8")
        return str(path)

    def test_event_file_title_and_body(self):
        repo = Repo(self)
        event = self.event_file(repo, {"title": "fix " + NAME, "body": "a\n" + EM, "number": 1})
        code, out = repo.scan(
            "--checks", "private-name,em-dash", "--names-file", repo.names_file(NAME), "--pr-event", event,
            env={"PR_TITLE": "", "PR_BODY": ""},
        )
        self.assertEqual(code, 1, out)
        self.assertIn("title line 1: private-name", out)
        self.assertIn("body line 2: em-dash", out)
        self.assertNotIn(NAME, out.lower())

    def test_event_file_null_body_is_empty(self):
        repo = Repo(self)
        code, out = repo.scan(*PUBLIC, "--pr-event", self.event_file(repo, {"title": "t", "body": None}))
        self.assertEqual(code, 0, out)

    def test_event_file_ignores_the_environment(self):
        repo = Repo(self)
        event = self.event_file(repo, {"title": "t", "body": "b"})
        code, out = repo.scan(*PUBLIC, "--pr-event", event, env={"PR_TITLE": EM, "PR_BODY": EM})
        self.assertEqual(code, 0, out)

    def test_event_file_without_a_pull_request_is_a_usage_error(self):
        repo = Repo(self)
        path = repo.outside / "event.json"
        path.write_text('{"ref": "refs/heads/main"}', encoding="utf-8")
        code, out = repo.scan(*PUBLIC, "--pr-event", str(path))
        self.assertEqual(code, 2, out)
        self.assertIn("--pr-event: no pull_request", out)

    def test_pr_event_and_pr_from_env_exclude_each_other(self):
        repo = Repo(self)
        code, out = repo.scan(*PUBLIC, "--pr-from-env", "--pr-event", self.event_file(repo, {"title": "t", "body": ""}))
        self.assertEqual(code, 2, out)
        self.assertIn("not allowed with", out)


class Links(unittest.TestCase):
    def links(self, files):
        repo = Repo(self, files)
        return repo.scan("--checks", "link")

    def test_s12_links_and_anchors_from_the_repo_root(self):
        listed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, check=True, env=git_env()
        ).stdout.decode().split("\0")
        tree = {p: (REPO_ROOT / p).read_bytes() for p in listed if p and (REPO_ROOT / p).is_file()}
        for link, fails in (
            ("ci-checks/README.md#流程级", False),
            ("ci-checks/README.md#没有这个", True),
            ("missing/README.md", True),
        ):
            with self.subTest(link=link):
                code, out = self.links({**tree, "a.md": "[x](%s)\n" % link})
                self.assertEqual(code, 1 if fails else 0, out)
                if fails:
                    self.assertIn("a.md:1:1: link (hygiene)", out)

    def test_s13_this_repos_links_pass(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--checks", "link", "--repo", str(REPO_ROOT)],
            capture_output=True, text=True, env=git_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertRegex(proc.stdout, r"link\s+hygiene\s+0")

    def test_anchor_rules(self):
        doc = (
            "# Top\n## 1. 设置\n## 死代码（knip）\n## `.github/` 改动单独成 PR\n"
            "## Dup\n## Dup\n## [Linked](x.md) *em*\nSetext Title\n---\n"
            "```\n## Not a heading\n```\n## `Vec<T>`\n## _italic_\n# Using _ as placeholder\n## a_b_\n## _private\n## Set APP__DB__HOST\n## ___x___\n## x __ y __ z\n"
        )
        for anchor, fails in (
            ("top", False),
            ("1-设置", False),
            ("死代码knip", False),
            ("github-改动单独成-pr", False),
            ("dup", False),
            ("dup-1", False),
            ("dup-2", True),
            ("linked-em", False),
            ("setext-title", False),
            ("not-a-heading", True),
            ("%E8%AE%BE%E7%BD%AE", True),
            ("1-%E8%AE%BE%E7%BD%AE", False),
            ("Top", True),
            ("vect", False),
            ("italic", False),
            ("using-_-as-placeholder", False),
            ("a_b_", False),
            ("_private", False),
            ("set-app__db__host", False),
            ("x", False),
            ("x-__-y-__-z", False),
        ):
            with self.subTest(anchor=anchor):
                code, out = self.links({"x.md": doc, "a.md": "see [x](x.md#%s)\n" % anchor})
                self.assertEqual(code, 1 if fails else 0, out)

    def test_same_file_anchor(self):
        self.assertEqual(self.links({"a.md": "# Hi\n[x](#hi)\n"})[0], 0)
        self.assertEqual(self.links({"a.md": "# Hi\n[x](#ho)\n"})[0], 1)

    def test_targets(self):
        for link, fails in (
            ("docs/", False),
            ("docs", False),
            ("./docs/b.md", False),
            ("docs/b.md \"title\"", False),
            ("<docs/b.md>", False),
            ("docs/missing.md", True),
            ("../outside.md", True),
            ("/docs/b.md", False),
            ("docs/b.md#L1", True),
            ("tool.py#L10", False),
            ("https://example.com/x.md", False),
            ("mailto:a" + AT + "example.com", False),
            ("//example.com/x", False),
        ):
            with self.subTest(link=link):
                code, out = self.links({"docs/b.md": "# B\n", "tool.py": "x\n", "a.md": "[x](%s)\n" % link})
                self.assertEqual(code, 1 if fails else 0, out)

    def test_link_forms(self):
        for text, fails in (
            ("![img](missing.png)", True),
            ("[ref]: missing.md", True),
            ("[ref]: b.md", False),
            ("`[x](missing.md)`", False),
            ("[^1]: See the notes", False),
            ("```\n```python\n[x](missing.md)\n```", False),
            ("```\n[x](missing.md)\n```", False),
            ("~~~\n[x](missing.md)\n~~~", False),
            ("text [x](b.md) and [y](missing.md)", True),
        ):
            with self.subTest(text=text):
                code, out = self.links({"b.md": "b\n", "a.md": text + "\n"})
                self.assertEqual(code, 1 if fails else 0, out)

    def test_only_markdown_files_are_checked(self):
        self.assertEqual(self.links({"a.txt": "[x](missing.md)\n"})[0], 0)

    def test_link_hits_are_hygiene_and_counted(self):
        code, out = self.links({"a.md": "[x](m1.md) [y](m2.md)\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("a.md:1:12: link (hygiene)", out)
        self.assertRegex(out, r"link\s+hygiene\s+2")


ZERO = "0" * 40


class PrePush(unittest.TestCase):
    """`--pre-push origin` with git's hook input on stdin; the Repo's origin/main is what the remote has."""

    def push(self, repo, *updates, names=(NAME,), checks=None):
        stdin = "".join(" ".join(u) + "\n" for u in updates)
        env = {SECRET_ENV: "\n".join(names)} if names else None
        args = ("--checks", checks) if checks else ()
        return repo.scan(*args, "--pre-push", "origin", env=env, stdin=stdin)

    def main_update(self, repo, ref="refs/heads/main"):
        return ("refs/heads/main", repo.git("rev-parse", "HEAD"), ref, repo.git("rev-parse", "origin/main"))

    def entry(self, repo, sha, path):
        listing = repo.git("diff-tree", "-r", "--root", "-c", "--no-commit-id", "--name-only", sha)
        return listing.splitlines().index(path) + 1

    def test_hit_in_a_pushed_file_names_its_commit_and_entry(self):
        repo = Repo(self)
        repo.write({"a.md": "clean\n", "b.md": "x\nsee " + HOME_BOB + "\n"})
        sha = repo.commit("add")
        code, out = self.push(repo, self.main_update(repo))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s file #%d:2:5: local-path (leak)" % (sha, self.entry(repo, sha, "b.md")), out)
        self.assertNotIn("b.md", out)
        self.assertIn("files with hits: 1, total hits: 1", out)

    def test_hit_in_a_pushed_path(self):
        repo = Repo(self)
        repo.write({"docs/" + NAME + ".md": "clean\n"})
        sha = repo.commit("add")
        code, out = self.push(repo, self.main_update(repo))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s file #1 path: private-name (leak)" % sha, out)
        self.assertNotIn(NAME, out.lower())

    def test_default_checks_are_the_leak_class(self):
        repo = Repo(self)
        repo.write({"a.md": EM + " [x](missing.md)\n"})
        repo.commit("add " + EM)
        code, out = self.push(repo, self.main_update(repo))
        self.assertEqual(code, 0, out)
        self.assertIsNone(summary_count(out, "em-dash"))
        self.assertEqual(summary_count(out, "private-name"), ("leak", "0"))

    def test_content_the_remote_has_or_the_push_leaves_unchanged_is_not_scanned(self):
        repo = Repo(self, {"old.md": "about " + NAME + "\n"})
        repo.write({"new.md": "clean\n"})
        repo.commit("add")
        code, out = self.push(repo, self.main_update(repo))
        self.assertEqual(code, 0, out)

    def test_a_file_added_then_deleted_inside_the_push_is_found(self):
        repo = Repo(self)
        repo.write({"tmp.md": "mail " + BOB_MAIL + "\n"})
        sha = repo.commit("add")
        repo.git("rm", "-q", "tmp.md")
        repo.commit("remove")
        code, out = self.push(repo, self.main_update(repo))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s file #1:1:6: email (leak)" % sha, out)
        self.assertNotIn(BOB_MAIL, out)

    def test_deletions_gitlinks_and_non_text_files(self):
        repo = Repo(self)
        repo.write({"bin.dat": b"\xff\xfe" + HOME_BOB.encode()})
        repo.git("rm", "-q", "README.md")
        repo.git("update-index", "--add", "--cacheinfo", "160000,%s,sub" % repo.git("rev-parse", "HEAD"))
        repo.commit("mixed")
        code, out = self.push(repo, self.main_update(repo))
        self.assertEqual(code, 0, out)
        self.assertIn("skipped non-text files: 1", out)

    def test_pushed_commit_messages_and_emails(self):
        repo = Repo(self)
        sha = repo.commit("x\n\nabout " + NAME, email=BOB_MAIL)
        code, out = self.push(repo, self.main_update(repo))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s line 3: private-name" % sha, out)
        self.assertIn("commit %s author: email" % sha, out)
        self.assertNotIn(NAME, out.lower())

    def test_new_branch_skips_what_the_remote_tracking_refs_reach(self):
        repo = Repo(self)
        repo.commit("old " + HOME_BOB)
        repo.git("update-ref", "refs/remotes/origin/main", "HEAD")
        sha = repo.commit("new " + HOME_BOB)
        code, out = self.push(repo, ("refs/heads/main", sha, "refs/heads/topic", ZERO))
        self.assertEqual(code, 1, out)
        self.assertIn("total hits: 1", out)
        self.assertIn("commit %s line 1: local-path" % sha, out)

    def test_remote_sha_from_the_input_excludes_its_history(self):
        repo = Repo(self)
        old = repo.commit("old " + HOME_BOB)
        new = repo.commit("new " + HOME_BOB)
        code, out = self.push(repo, ("refs/heads/topic", new, "refs/heads/topic", old))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s line 1: local-path" % new, out)
        self.assertIn("total hits: 1", out)

    def test_ref_names_with_unicode_line_separators(self):
        repo = Repo(self)
        sha = repo.commit("new " + HOME_BOB)
        code, out = self.push(repo, ("refs/heads/a\u0085b", sha, "refs/heads/a\u2028b", ZERO))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s line 1: local-path" % sha, out)

    def test_remote_sha_missing_here_is_ignored(self):
        repo = Repo(self)
        sha = repo.commit("new " + HOME_BOB)
        code, out = self.push(repo, ("refs/heads/main", sha, "refs/heads/main", "1" * 40))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s line 1: local-path" % sha, out)

    def test_local_ref_with_spaces(self):
        repo = Repo(self)
        sha = repo.commit("new " + HOME_BOB)
        code, out = self.push(repo, ("HEAD@{0 minutes ago}", sha, "refs/heads/x", ZERO))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s line 1: local-path" % sha, out)

    def test_each_ref_of_a_multi_ref_push(self):
        repo = Repo(self)
        clean = repo.commit("clean")
        repo.git("checkout", "-q", "-b", "other", "origin/main")
        leak = repo.commit("see " + HOME_BOB)
        code, out = self.push(repo, ("refs/heads/main", clean, "refs/heads/main", ZERO),
                              ("refs/heads/other", leak, "refs/heads/other", ZERO))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s line 1: local-path" % leak, out)
        self.assertIn("total hits: 1", out)

    def test_annotated_tag_push_scans_the_tagged_commits(self):
        repo = Repo(self)
        sha = repo.commit("see " + HOME_BOB)
        repo.git("tag", "-a", "v1", "-m", "release")
        code, out = self.push(repo, ("refs/tags/v1", repo.git("rev-parse", "v1"), "refs/tags/v1", ZERO))
        self.assertEqual(code, 1, out)
        self.assertIn("commit %s line 1: local-path" % sha, out)

    def test_ref_naming_a_tree_or_blob_is_a_usage_error(self):
        repo = Repo(self)
        tree = repo.git("rev-parse", "HEAD^{tree}")
        blob = repo.git("rev-parse", "HEAD:README.md")
        repo.git("tag", "-a", "t", "-m", "x", tree)
        for sha in (tree, blob, repo.git("rev-parse", "t")):
            with self.subTest(sha=sha):
                code, out = self.push(repo, ("refs/tags/t", sha, "refs/tags/t", ZERO))
                self.assertEqual(code, 2, out)

    def test_delete_only_push_passes(self):
        repo = Repo(self)
        code, out = self.push(repo, ("(delete)", ZERO, "refs/heads/old", repo.git("rev-parse", "HEAD")))
        self.assertEqual(code, 0, out)

    def test_unavailable_list_fails_closed(self):
        repo = Repo(self)
        repo.commit("clean")
        code, out = self.push(repo, self.main_update(repo), names=())
        self.assertEqual(code, 1, out)
        self.assertIn("unavailable", out)

    def test_notice_says_nothing_was_published(self):
        repo = Repo(self)
        repo.commit("see " + HOME_BOB)
        code, out = self.push(repo, self.main_update(repo))
        self.assertEqual(code, 1, out)
        self.assertNotIn("already public", out)
        self.assertIn("Nothing was pushed", out)

    def test_link_check_is_a_usage_error(self):
        repo = Repo(self)
        code, out = self.push(repo, self.main_update(repo), checks="link")
        self.assertEqual(code, 2, out)

    def test_malformed_input_is_a_usage_error(self):
        repo = Repo(self)
        code, out = repo.scan("--pre-push", "origin", stdin="garbage\n", env={SECRET_ENV: NAME})
        self.assertEqual(code, 2, out)

if __name__ == "__main__":
    unittest.main()
