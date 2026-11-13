"""Tests for gitleaks_report.py, the formatter of ci.yml's gitleaks reports."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "guards/leak-scan/scanner/gitleaks_report.py"
CLEAN_MAIL = "t" + "@" + "example.com"


def git(top, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL=CLEAN_MAIL,
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL=CLEAN_MAIL)
    return subprocess.run(["git", *args], cwd=top, env=env, check=True, capture_output=True, text=True).stdout.strip()


def finding(file, rule="generic-api-key", line=3, col=7, commit=""):
    return {"File": file, "RuleID": rule, "StartLine": line, "StartColumn": col, "Commit": commit}


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.top = self.tmp / "tree" / "repo"
        self.top.mkdir(parents=True)
        git(self.top, "init", "-q", "-b", "main")
        for name in ("a.txt", "b/c.txt", "d.txt"):
            (self.top / name).parent.mkdir(parents=True, exist_ok=True)
            (self.top / name).write_text("x\n")
        git(self.top, "add", "-A")
        git(self.top, "commit", "-q", "-m", "one")
        self.first = git(self.top, "rev-parse", "HEAD")
        git(self.top, "rm", "-q", "a.txt")
        git(self.top, "commit", "-q", "-m", "two")
        self.output = self.tmp / "github_output"
        self.output.write_text("earlier=1\n")

    def run_report(self, files=(), commits=(), messages=()):
        for scan, findings in (("files", files), ("commits", commits), ("messages", messages)):
            (self.tmp / f"gitleaks-{scan}.json").write_text(json.dumps(list(findings)))
        env = dict(os.environ, GITHUB_OUTPUT=str(self.output))
        proc = subprocess.run([sys.executable, str(SCRIPT), str(self.top), str(self.tmp)],
                              env=env, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def test_no_findings(self):
        self.assertEqual(self.run_report(), (0, "", ""))
        self.assertEqual(self.output.read_text(), "earlier=1\nhits=0\n")

    def test_a_file_hit_is_named_by_its_index_at_head(self):
        code, out, _ = self.run_report(files=[finding(str(self.top / "d.txt"))])
        self.assertEqual((code, out), (0, "file #2:3:7: gitleaks (leak) rule generic-api-key\n"))
        self.assertTrue(self.output.read_text().endswith("hits=1\n"))

    def test_a_commit_hit_is_named_by_its_index_in_that_commit(self):
        code, out, _ = self.run_report(commits=[finding("b/c.txt", line=1, col=2, commit=self.first)])
        self.assertEqual(out, f"commit {self.first} file #2:1:2: gitleaks (leak) rule generic-api-key\n")

    def test_a_path_missing_from_the_list_gives_only_the_line(self):
        _, out, _ = self.run_report(files=[finding(str(self.top / "gone.txt"), line=9)])
        self.assertEqual(out, "line 9: gitleaks (leak) rule generic-api-key\n")

    def test_a_message_hit_is_named_by_its_commit(self):
        _, out, _ = self.run_report(messages=[finding(str(self.tmp / "messages" / "m" / self.first), line=4)])
        self.assertEqual(out, f"commit {self.first} line 4: gitleaks (leak) rule generic-api-key\n")

    def test_hits_are_counted_across_scans_in_scan_order(self):
        code, out, _ = self.run_report(
            files=[finding(str(self.top / "b/c.txt"))],
            commits=[finding("a.txt", commit=self.first)],
            messages=[finding("m/" + self.first, rule="r")],
        )
        self.assertEqual(code, 0)
        self.assertEqual([line.split(":")[0] for line in out.splitlines()],
                         ["file #1", f"commit {self.first} file #1", f"commit {self.first} line 3"])
        self.assertTrue(self.output.read_text().endswith("hits=3\n"))

    def test_a_missing_report_fails(self):
        (self.tmp / "gitleaks-messages.json").unlink(missing_ok=True)
        for scan in ("files", "commits"):
            (self.tmp / f"gitleaks-{scan}.json").write_text("[]")
        env = dict(os.environ, GITHUB_OUTPUT=str(self.output))
        proc = subprocess.run([sys.executable, str(SCRIPT), str(self.top), str(self.tmp)], env=env, capture_output=True)
        self.assertNotEqual(proc.returncode, 0)

    def test_wrong_argument_count_is_a_usage_error(self):
        proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage:", proc.stderr)


if __name__ == "__main__":
    unittest.main()
