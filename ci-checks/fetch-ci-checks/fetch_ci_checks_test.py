import io
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from fetch_ci_checks import Error, caller_repo, run

SHA = "0123456789abcdef0123456789abcdef01234567"


def caller(*refs: str, org: str = "example-org") -> str:
    jobs = "".join(
        f"  {lang}:\n    uses: {org}/repo-guards/.github/workflows/{lang}.yml@{ref}\n"
        for lang, ref in zip(("rust", "python", "typescript"), refs)
    )
    return f"on: pull_request\njobs:\n{jobs}"


def archive(files: dict[str, str], commit: str | None = SHA) -> bytes:
    buffer = io.BytesIO()
    headers = {"comment": commit} if commit else {}
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers=headers) as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"repo-guards-main/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class CallerRepo(unittest.TestCase):
    def test_reads_repo_of_main_callers(self):
        self.assertEqual(caller_repo(caller("main", "main")), "example-org/repo-guards")

    def test_rejects_missing_non_main_and_mixed_refs(self):
        workflows = (
            "on: push\n",
            caller(SHA),
            caller(SHA[:7]),
            caller("release"),
            caller("main", SHA),
            caller("main") + caller("main", org="other-org"),
        )
        for workflow in workflows:
            with self.assertRaises(Error, msg=workflow):
                caller_repo(workflow)


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.workflow = self.tmp / "ci.yml"
        self.out = self.tmp / ".repo-guards"

    def run_fetch(self, workflow: str, tarball: bytes) -> tuple[int, list[tuple[str, str]]]:
        self.workflow.write_text(workflow)
        calls: list[tuple[str, str]] = []

        def fetch(repo: str, ref: str) -> bytes:
            calls.append((repo, ref))
            return tarball

        code = run(["--workflow", str(self.workflow), "--out", str(self.out)], fetch)
        return code, calls

    def test_extracts_only_ci_checks_from_main_and_records_its_commit(self):
        code, calls = self.run_fetch(
            caller("main", "main", "main"),
            archive({"ci-checks/rust/deny.toml": "[bans]\n", "README.md": "top\n", ".github/workflows/rust.yml": "x\n"}),
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("example-org/repo-guards", "main")])
        self.assertEqual((self.out / "ci-checks/rust/deny.toml").read_text(), "[bans]\n")
        self.assertEqual((self.out / "SHA").read_text(), SHA + "\n")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [".gitignore", "SHA", "ci-checks"])

    def test_writes_gitignore_that_ignores_the_whole_output_dir(self):
        code, _ = self.run_fetch(caller("main"), archive({"ci-checks/a.toml": "1\n"}))
        self.assertEqual(code, 0)
        self.assertEqual((self.out / ".gitignore").read_text(), "*\n")

    def test_refetch_replaces_old_files(self):
        self.run_fetch(caller("main"), archive({"ci-checks/old.toml": "1\n"}))
        self.run_fetch(caller("main"), archive({"ci-checks/new.toml": "2\n"}))
        self.assertEqual(sorted(p.name for p in (self.out / "ci-checks").iterdir()), ["new.toml"])

    def test_non_main_ref_downloads_nothing(self):
        code, calls = self.run_fetch(caller(SHA), archive({"ci-checks/a": "x"}))
        self.assertEqual((code, calls), (2, []))
        self.assertFalse(self.out.exists())

    def test_bad_archive_keeps_old_files(self):
        self.run_fetch(caller("main"), archive({"ci-checks/keep.toml": "1\n"}))
        for tarball in (archive({"README.md": "x\n"}), archive({"ci-checks/a.toml": "1\n"}, commit=None)):
            code, _ = self.run_fetch(caller("main"), tarball)
            self.assertEqual(code, 2)
            self.assertEqual(sorted(p.name for p in (self.out / "ci-checks").iterdir()), ["keep.toml"])
            self.assertEqual((self.out / "SHA").read_text(), SHA + "\n")


if __name__ == "__main__":
    unittest.main()
