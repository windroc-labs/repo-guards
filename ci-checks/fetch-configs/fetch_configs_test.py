import io
import shutil
import tarfile
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from fetch_configs import Error, caller_repo, run

SHA = "0123456789abcdef0123456789abcdef01234567"
SCRIPT = "ci-checks/config-overrides/config_overrides.py"
REAL_SCRIPT = (
    Path(__file__).resolve().parents[1] / "config-overrides" / "config_overrides.py"
).read_text()


def caller(*refs: str, org: str = "example-org") -> str:
    jobs = "".join(
        f"  {lang}:\n    uses: {org}/repo-guards/.github/workflows/{lang}.yml@{ref}\n"
        for lang, ref in zip(("rust", "python", "typescript"), refs)
    )
    return f"on: pull_request\njobs:\n{jobs}"


def archive(
    files: dict[str, str], commit: str | None = SHA, script: str | None = REAL_SCRIPT
) -> bytes:
    if script is not None:
        files = {SCRIPT: script, **files}
    buffer = io.BytesIO()
    headers = {"comment": commit} if commit else {}
    with tarfile.open(
        fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers=headers
    ) as tar:
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
        self.overrides = self.tmp / "repo-guards.toml"

    def run_fetch(self, workflow: str, tarball: bytes) -> tuple[int, list[tuple[str, str]]]:
        self.workflow.write_text(workflow)
        calls: list[tuple[str, str]] = []

        def fetch(repo: str, ref: str) -> bytes:
            calls.append((repo, ref))
            return tarball

        args = [
            "--workflow",
            str(self.workflow),
            "--out",
            str(self.out),
            "--overrides",
            str(self.overrides),
        ]
        code = run(args, fetch)
        return code, calls

    def test_extracts_only_ci_checks_from_main_and_records_its_commit(self):
        code, calls = self.run_fetch(
            caller("main", "main", "main"),
            archive(
                {
                    "ci-checks/rust/deny.toml": "[bans]\n",
                    "README.md": "top\n",
                    ".github/workflows/rust.yml": "x\n",
                }
            ),
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("example-org/repo-guards", "main")])
        self.assertEqual((self.out / "ci-checks/rust/deny.toml").read_text(), "[bans]\n")
        self.assertEqual((self.out / "SHA").read_text(), SHA + "\n")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["SHA", "ci-checks"])

    def test_refetch_replaces_old_files(self):
        self.run_fetch(caller("main"), archive({"ci-checks/old.toml": "1\n"}))
        self.run_fetch(caller("main"), archive({"ci-checks/new.toml": "2\n"}))
        self.assertEqual(
            sorted(p.name for p in (self.out / "ci-checks").iterdir()),
            ["config-overrides", "new.toml"],
        )

    def test_applies_the_callers_overrides(self):
        self.overrides.write_text('["ci-checks/coverage/coverage.toml"]\ntotal-min = 70\n')
        code, _ = self.run_fetch(
            caller("main"),
            archive({"ci-checks/coverage/coverage.toml": "total-min = 80\ndiff-min = 90\n"}),
        )
        self.assertEqual(code, 0)
        coverage = (self.out / "ci-checks/coverage/coverage.toml").read_text()
        self.assertEqual(tomllib.loads(coverage), {"total-min": 70, "diff-min": 90})

    def test_runs_the_downloaded_script(self):
        stub = "import pathlib, sys\npathlib.Path(sys.argv[2], 'ran').write_text(' '.join(sys.argv[1:]))\n"
        code, _ = self.run_fetch(caller("main"), archive({"ci-checks/a.toml": "1\n"}, script=stub))
        self.assertEqual(code, 0)
        self.assertTrue((self.out / "ran").read_text().startswith("apply "))
        self.assertTrue((self.out / "ran").read_text().endswith(f" {self.overrides}"))

    def test_failed_apply_keeps_old_files(self):
        self.run_fetch(caller("main"), archive({"ci-checks/keep.toml": "k = 1\n"}))
        self.overrides.write_text('["ci-checks/missing.toml"]\nk = 2\n')
        err = io.StringIO()
        with redirect_stderr(err):
            code, _ = self.run_fetch(caller("main"), archive({"ci-checks/keep.toml": "k = 3\n"}))
        self.assertEqual(code, 2)
        self.assertIn('["ci-checks/missing.toml"]', err.getvalue())
        self.assertEqual((self.out / "ci-checks/keep.toml").read_text(), "k = 1\n")
        self.assertEqual(
            sorted(p.name for p in self.tmp.iterdir()),
            [".repo-guards", "ci.yml", "repo-guards.toml"],
        )

    def test_non_main_ref_downloads_nothing(self):
        code, calls = self.run_fetch(caller(SHA), archive({"ci-checks/a": "x"}))
        self.assertEqual((code, calls), (2, []))
        self.assertFalse(self.out.exists())

    def test_bad_archive_keeps_old_files(self):
        self.run_fetch(caller("main"), archive({"ci-checks/keep.toml": "1\n"}))
        for tarball in (
            archive({"README.md": "x\n"}, script=None),
            archive({"ci-checks/a.toml": "1\n"}, commit=None),
        ):
            code, _ = self.run_fetch(caller("main"), tarball)
            self.assertEqual(code, 2)
            self.assertEqual(
                sorted(p.name for p in (self.out / "ci-checks").iterdir()),
                ["config-overrides", "keep.toml"],
            )
            self.assertEqual((self.out / "SHA").read_text(), SHA + "\n")


if __name__ == "__main__":
    unittest.main()
