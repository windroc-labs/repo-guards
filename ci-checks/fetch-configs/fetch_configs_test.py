import io
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from fetch_configs import Error, pinned, run

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"


def caller(*refs: str) -> str:
    jobs = "".join(
        f"  {lang}:\n    uses: example-org/repo-guards/.github/workflows/{lang}.yml@{ref}\n"
        for lang, ref in zip(("rust", "python", "typescript"), refs)
    )
    return f"on: pull_request\njobs:\n{jobs}"


def archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"repo-guards-{SHA}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class Pinned(unittest.TestCase):
    def test_reads_repo_and_sha(self):
        self.assertEqual(pinned(caller(SHA, SHA)), ("example-org/repo-guards", SHA))

    def test_rejects_missing_mixed_and_non_sha_refs(self):
        for workflow in ("on: push\n", caller(SHA, OTHER), caller("main"), caller(SHA[:7])):
            with self.assertRaises(Error, msg=workflow):
                pinned(workflow)


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.workflow = self.tmp / "ci.yml"
        self.out = self.tmp / ".repo-guards"

    def run_fetch(
        self, workflow: str, files: dict[str, str], status: str = "ahead"
    ) -> tuple[int, list[tuple[str, str]]]:
        self.workflow.write_text(workflow)
        calls: list[tuple[str, str]] = []

        def fetch(repo: str, sha: str) -> bytes:
            calls.append((repo, sha))
            return archive(files)

        def compare(repo: str, sha: str) -> str:
            self.assertEqual((repo, sha), ("example-org/repo-guards", SHA))
            return status

        code = run(["--workflow", str(self.workflow), "--out", str(self.out)], fetch, compare)
        return code, calls

    def test_sha_on_main_is_fetched(self):
        for status in ("ahead", "identical"):
            code, calls = self.run_fetch(caller(SHA), {"ci-checks/a.toml": "1\n"}, status)
            self.assertEqual((code, calls), (0, [("example-org/repo-guards", SHA)]), status)

    def test_sha_not_on_main_downloads_nothing(self):
        for status in ("diverged", "behind"):
            code, calls = self.run_fetch(caller(SHA), {"ci-checks/a.toml": "1\n"}, status)
            self.assertEqual((code, calls), (2, []), status)
            self.assertFalse(self.out.exists(), status)

    def test_extracts_only_ci_checks_at_the_pinned_sha(self):
        code, calls = self.run_fetch(
            caller(SHA, SHA, SHA),
            {"ci-checks/rust/deny.toml": "[bans]\n", "README.md": "top\n", ".github/workflows/rust.yml": "x\n"},
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("example-org/repo-guards", SHA)])
        self.assertEqual((self.out / "ci-checks/rust/deny.toml").read_text(), "[bans]\n")
        self.assertEqual((self.out / "SHA").read_text(), SHA + "\n")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["SHA", "ci-checks"])

    def test_refetch_replaces_old_files(self):
        self.run_fetch(caller(SHA), {"ci-checks/old.toml": "1\n"})
        self.run_fetch(caller(SHA), {"ci-checks/new.toml": "2\n"})
        self.assertEqual(sorted(p.name for p in (self.out / "ci-checks").iterdir()), ["new.toml"])

    def test_bad_pin_downloads_nothing(self):
        code, calls = self.run_fetch(caller("main"), {"ci-checks/a": "x"})
        self.assertEqual((code, calls), (2, []))
        self.assertFalse(self.out.exists())

    def test_archive_without_ci_checks_keeps_old_files(self):
        self.run_fetch(caller(SHA), {"ci-checks/keep.toml": "1\n"})
        code, _ = self.run_fetch(caller(SHA), {"README.md": "x\n"})
        self.assertEqual(code, 2)
        self.assertTrue((self.out / "ci-checks/keep.toml").exists())


if __name__ == "__main__":
    unittest.main()
