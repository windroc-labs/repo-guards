import io
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[3] / "guards/ci-checks/checkout-unchanged"
sys.path.insert(0, str(SOURCE))

import checkout_unchanged

OVERRIDES = ".github/checks/repo-guards.toml"
ENFORCE = '["ci-checks/checkout-unchanged/settings.toml"]\nenforce = true\n'


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = checkout_unchanged.main([str(arg) for arg in argv])
    return code, out.getvalue(), err.getvalue()


class Repo(unittest.TestCase):
    """A committed repo that ignores node_modules/, cache/ and *.log; `overrides` is its repo-guards.toml at HEAD."""

    overrides = ENFORCE

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "repo"
        self.root.mkdir()
        self.file = Path(tmp.name) / "installed-files"
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "ci@example.com")
        git(self.root, "config", "user.name", "ci")
        self.write(".gitignore", "node_modules/\ncache/\n*.log\n")
        self.write("src/a.py", "A = 1\n")
        self.write("src/b.py", "B = 1\n")
        if self.overrides is not None:
            self.write(OVERRIDES, self.overrides)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")

    def write(self, path, text="x\n"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def check(self, *extra):
        return run("check", self.root, *extra)

    def changed(self, *extra):
        """The (code, path) lines of a failing check."""
        code, out, err = self.check(*extra)
        self.assertEqual((code, err), (1, ""), out)
        return [line for line in out.splitlines() if line.startswith("  ")]


class Adopted(Repo):
    def test_a_clean_checkout_passes(self):
        self.assertEqual(self.check(), (0, "ok checkout unchanged\n", ""))

    def test_a_modified_tracked_file_fails(self):
        self.write("src/a.py", "A = 2\n")
        code, out, err = self.check()
        self.assertEqual((code, err), (1, ""))
        lines = out.splitlines()
        self.assertEqual(lines[0], "FAIL checkout unchanged: 1 path changed or added since checkout")
        self.assertEqual(lines[1], '   M "src/a.py"')
        self.assertEqual(lines[2], checkout_unchanged.HINT)
        self.assertEqual(len(lines), 3)

    def test_a_deleted_tracked_file_fails(self):
        (self.root / "src/a.py").unlink()
        self.assertEqual(self.changed(), ['   D "src/a.py"'])

    def test_a_staged_change_fails(self):
        self.write("src/a.py", "A = 2\n")
        git(self.root, "add", "src/a.py")
        self.assertEqual(self.changed(), ['  M  "src/a.py"'])

    def test_a_staged_rename_is_one_path(self):
        git(self.root, "mv", "src/a.py", "src/c.py")
        self.assertEqual(self.changed(), ['  R  "src/c.py"'])

    def test_an_untracked_file_fails(self):
        self.write("out/result.xml")
        self.assertEqual(self.changed(), ['  ?? "out/result.xml"'])

    def test_ignored_files_fail_one_line_each(self):
        self.write("cache/a/one")
        self.write("cache/two")
        self.write("src/run.log")
        self.assertEqual(self.changed(), ['  !! "cache/a/one"', '  !! "cache/two"', '  !! "src/run.log"'])

    def test_paths_sort_by_path_and_count_in_the_first_line(self):
        self.write("z.txt")
        self.write("src/a.py", "A = 2\n")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("2 paths changed or added", out.splitlines()[0])
        self.assertEqual(out.splitlines()[1:3], ['   M "src/a.py"', '  ?? "z.txt"'])

    def test_a_path_that_looks_like_a_workflow_command_stays_on_one_quoted_line(self):
        self.write("::error::x\ny")
        self.assertEqual(self.changed(), ['  ?? "::error::x\\ny"'])

    def test_a_non_utf8_name_is_escaped(self):
        (self.root / b"caf\xe9".decode("utf-8", "surrogateescape")).write_text("x")
        self.assertEqual(self.changed(), ['  ?? "caf\\udce9"'])

    def test_more_than_fifty_paths_are_capped(self):
        for n in range(53):
            self.write(f"out/{n:02}.txt")
        lines = self.changed()
        self.assertEqual(len(lines), 51)
        self.assertEqual(lines[49], '  ?? "out/49.txt"')
        self.assertEqual(lines[50], "  ... and 3 more")

    def test_exactly_fifty_paths_are_all_listed(self):
        for n in range(50):
            self.write(f"out/{n:02}.txt")
        lines = self.changed()
        self.assertEqual(len(lines), 50)
        self.assertEqual(lines[-1], '  ?? "out/49.txt"')


class Installed(Repo):
    def install(self):
        self.write("node_modules/dep/index.js")
        self.write("packages/a/node_modules/dep/index.js")
        self.assertEqual(run("installed", self.root, self.file), (0, "", ""))

    def test_node_modules_from_the_install_passes(self):
        self.install()
        self.assertEqual(self.check(self.file), (0, "ok checkout unchanged\n", ""))

    def test_without_the_file_argument_node_modules_fails(self):
        self.install()
        self.assertEqual(len(self.changed()), 2)

    def test_a_missing_file_allows_nothing(self):
        self.write("node_modules/dep/index.js")
        self.assertEqual(self.changed(self.file), ['  !! "node_modules/dep/index.js"'])

    def test_a_file_added_to_node_modules_later_fails(self):
        self.install()
        self.write("node_modules/.vite/results.json")
        self.write("packages/a/node_modules/dep/extra.js")
        self.assertEqual(
            self.changed(self.file),
            ['  !! "node_modules/.vite/results.json"', '  !! "packages/a/node_modules/dep/extra.js"'],
        )

    def test_node_modules_that_is_not_ignored_passes_as_untracked(self):
        self.write(".gitignore", "cache/\n")
        git(self.root, "commit", "-q", "-a", "-m", "track")
        self.install()
        self.assertEqual(self.check(self.file), (0, "ok checkout unchanged\n", ""))

    def test_other_paths_present_at_install_still_fail(self):
        self.write("cache/built")
        self.write("out.txt")
        self.write("my_node_modules/x")
        self.write("src/a.py", "A = 2\n")
        self.install()
        self.assertEqual(
            self.changed(self.file),
            ['  !! "cache/built"', '  ?? "my_node_modules/x"', '  ?? "out.txt"', '   M "src/a.py"'],
        )

    def test_a_tracked_file_under_node_modules_changed_at_install_still_fails(self):
        self.write("vendor/node_modules/x.js", "1\n")
        git(self.root, "add", "-f", "vendor/node_modules/x.js")
        git(self.root, "commit", "-q", "-m", "vendored")
        self.write("vendor/node_modules/x.js", "2\n")
        self.install()
        self.assertEqual(self.changed(self.file), ['   M "vendor/node_modules/x.js"'])

    def test_installed_overwrites_the_file(self):
        self.file.write_bytes(b"!! node_modules/stale\0")
        self.install()
        self.assertNotIn(b"stale", self.file.read_bytes())

    def test_an_unreadable_file_exits_2(self):
        self.file.mkdir()
        code, out, err = self.check(self.file)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("checkout-unchanged: ", err)

    def test_an_unwritable_file_exits_2(self):
        code, out, err = run("installed", self.root, self.file / "missing-dir" / "file")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("checkout-unchanged: ", err)


class NotAdopted(Repo):
    overrides = None

    def test_a_clean_checkout_says_not_adopted(self):
        self.assertEqual(self.check(), (0, "ok checkout unchanged (not adopted)\n", ""))

    def test_changes_are_reported_and_pass(self):
        self.write("out.txt")
        code, out, err = self.check()
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(
            lines[0],
            "report checkout unchanged (not adopted, CI does not fail): 1 path changed or added since checkout",
        )
        self.assertEqual(lines[1], '  ?? "out.txt"')
        self.assertEqual(lines[2], checkout_unchanged.ADOPT_HINT)

    def test_enforce_set_only_in_the_work_tree_does_not_adopt(self):
        self.write(OVERRIDES, ENFORCE)
        code, out, _ = self.check()
        self.assertEqual(code, 0)
        self.assertIn("report checkout unchanged (not adopted", out)
        self.assertIn(f'  ?? "{OVERRIDES}"', out)

    def test_the_shipped_default_does_not_enforce(self):
        self.assertEqual(checkout_unchanged.SETTINGS.read_text().count("enforce = false\n"), 1)


class EnforceRemovedInWorkTree(Repo):
    def test_head_decides_so_a_test_cannot_switch_the_check_off(self):
        (self.root / OVERRIDES).write_text("")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn(f'   M "{OVERRIDES}"', out)


class OtherSections(Repo):
    overrides = '["ci-checks/.jscpd.json"]\nminLines = 9\n\n[project."."]\nsource-dirs = ["src"]\n'

    def test_overrides_without_the_section_do_not_adopt(self):
        self.assertEqual(self.check(), (0, "ok checkout unchanged (not adopted)\n", ""))


class EnforceFalse(Repo):
    overrides = '["ci-checks/checkout-unchanged/settings.toml"]\nenforce = false\n'

    def test_enforce_false_reports(self):
        self.write("out.txt")
        self.assertEqual(self.check()[0], 0)


class BadSettings(Repo):
    overrides = '["ci-checks/checkout-unchanged/settings.toml"]\nenforce = "yes"\n'

    def expect_error(self, text, message):
        self.write(OVERRIDES, text)
        git(self.root, "commit", "-q", "-a", "-m", "settings")
        code, out, err = self.check()
        self.assertEqual((code, out), (2, ""))
        self.assertIn(message, err)

    def test_enforce_that_is_not_a_boolean_exits_2(self):
        code, out, err = self.check()
        self.assertEqual((code, out), (2, ""))
        self.assertIn("`enforce` must be true or false", err)

    def test_a_section_that_is_not_a_table_exits_2(self):
        self.expect_error('"ci-checks/checkout-unchanged/settings.toml" = true\n', "is not a table")

    def test_unparsable_overrides_exit_2(self):
        self.expect_error("[[[\n", OVERRIDES)

    def test_overrides_that_are_not_utf8_exit_2(self):
        (self.root / OVERRIDES).write_bytes(b'x = "\xff"\n')
        git(self.root, "commit", "-q", "-a", "-m", "settings")
        code, out, err = self.check()
        self.assertEqual((code, out), (2, ""))
        self.assertIn(OVERRIDES, err)


class Usage(unittest.TestCase):
    def test_bad_arguments_exit_2_with_usage(self):
        for argv in ([], ["check"], ["check", "a", "b", "c"], ["installed", "a"], ["installed", "a", "b", "c"], ["other", "a"]):
            with self.subTest(argv):
                code, out, err = run(*argv)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("usage: checkout_unchanged.py", err)

    def test_a_directory_that_is_not_a_repo_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            for argv in (["check", tmp], ["installed", tmp, Path(tmp) / "file"]):
                with self.subTest(argv[0]):
                    code, out, err = run(*argv)
                    self.assertEqual((code, out), (2, ""))
                    self.assertIn("checkout-unchanged: git ", err)


class Workflows(unittest.TestCase):
    """Every job of a language workflow that runs on the caller's checkout ends with the check; callers add nothing."""

    WORKFLOWS = SOURCE.parents[2] / ".github/workflows"
    LANGUAGES = ("go", "python", "typescript", "rust")
    JOBS = ("checks", "coverage", "coverage-shard", "api-snapshot")
    # The file `installed` wrote after the dependency install, where the workflow installs into the checkout.
    FILES = {"typescript": ' "$RUNNER_TEMP/installed-files"'}
    # Checks runs every check whatever the others found; inline_exemptions_test wants this gate on each of them.
    GATE = "if: ${{ !cancelled() && steps.inline-exemptions.outcome == 'success' }}\n"

    def jobs(self, language):
        text = (self.WORKFLOWS / f"{language}.yml").read_text().split("\njobs:\n", 1)[1]
        parts = re.split(r"^  ([a-z-]+):\n", text, flags=re.M)
        return dict(zip(parts[1::2], parts[2::2]))

    @staticmethod
    def last_step(job):
        return job.split("\n      - ")[-1]

    def test_every_job_but_the_gate_ends_with_the_check(self):
        for language in self.LANGUAGES:
            with self.subTest(language):
                jobs = self.jobs(language)
                self.assertEqual(list(jobs), ["changes", *self.JOBS])
                self.assertNotIn("checkout_unchanged.py", jobs["changes"])
                run = (
                    '        run: uv run --no-project --python 3.12 "$RG/checkout-unchanged/checkout_unchanged.py"'
                    f' check "$GITHUB_WORKSPACE/repo"{self.FILES.get(language, "")}\n'
                )
                checks, coverage = self.last_step(jobs["checks"]), self.last_step(jobs["coverage"])
                self.assertTrue(checks.startswith("name: Checkout unchanged\n"), checks)
                self.assertIn(self.GATE, checks)
                self.assertTrue(coverage.startswith("&checkout-unchanged\n        name: Checkout unchanged\n"), coverage)
                self.assertIn("if: ${{ !cancelled() }}\n", coverage)
                for step in (checks, coverage):
                    # From the runner's temp directory without uv config: neither the PR's uv settings nor its files are read.
                    self.assertIn("working-directory: ${{ runner.temp }}\n", step)
                    self.assertIn('UV_NO_CONFIG: "1"\n', step)
                    self.assertEqual(step.count(run), 1, step)
                for job in ("coverage-shard", "api-snapshot"):
                    self.assertEqual(self.last_step(jobs[job]).split("\n", 1)[0], "*checkout-unchanged")
                self.assertEqual(sum(job.count("checkout_unchanged.py\" check") for job in jobs.values()), 2)


    def test_python_jobs_write_outside_the_checkout(self):
        jobs = self.jobs("python")
        self.assertNotIn(".venv", "".join(jobs.values()))
        outside = self.jobs("python")["checks"].split("\n      - &outside\n", 1)[1].split("\n      - ", 1)[0]
        for name in ("UV_PROJECT_ENVIRONMENT", "PYTHONPYCACHEPREFIX", "RUFF_CACHE_DIR", "COVERAGE_FILE", "PYTEST_ADDOPTS"):
            self.assertRegex(outside, rf'echo "{name}=[^\n]*\$RUNNER_TEMP/')
        self.assertIn('>> "$GITHUB_ENV"', outside)
        for job in self.JOBS:
            # Before anything is installed or run, so no step writes where the check would find it.
            steps = [step.split("\n", 1)[0] for step in jobs[job].split("\n      - ")[1:]]
            self.assertEqual(steps[3].removeprefix("&").removeprefix("*"), "outside", job)


    def test_typescript_records_the_install_and_keeps_vitest_out_of_the_checkout(self):
        jobs = self.jobs("typescript")
        record = (
            'uv run --no-project --python 3.12 "$RG/checkout-unchanged/checkout_unchanged.py"'
            ' installed "$GITHUB_WORKSPACE/repo" "$RUNNER_TEMP/installed-files"'
        )
        install = jobs["checks"].split("\n      - &install-project\n", 1)[1].split("\n      - ", 1)[0]
        self.assertLess(install.index("npm ci --no-audit --no-fund\n"), install.index(record))
        for job in ("coverage", "coverage-shard"):
            self.assertIn("\n      - *install-project\n", jobs[job])
        # API snapshot installs inside Generate; a failed listing stops the job like a failed install.
        generate = jobs["api-snapshot"].split("\n      - name: Generate\n", 1)[1].split("\n      - ", 1)[0]
        self.assertLess(generate.index("npm ci --no-audit --no-fund || exit 2\n"), generate.index(f"{record} || exit 2\n"))
        self.assertLess(generate.index(record), generate.index("api-snapshot.ts"))
        self.assertEqual(sum(job.count(record) for job in jobs.values()), 2)
        coverage = jobs["coverage"].split("\n      - &coverage\n", 1)[1].split("\n      - ", 1)[0]
        flags = next(line for line in coverage.splitlines() if line.strip().startswith("flags=("))
        self.assertIn("--no-cache ", flags)
        self.assertIn('--coverage.reportsDirectory="$RUNNER_TEMP/coverage-head"', flags)
        self.assertIn("flags+=(--no-fsModuleCache)", coverage)
        runs = [line for line in coverage.splitlines() if "vitest run " in line and "--help" not in line]
        self.assertEqual(len(runs), 3)
        for line in runs:
            self.assertIn('"${flags[@]}"', line)


    def test_rust_builds_outside_the_checkout(self):
        jobs = self.jobs("rust")
        install = jobs["checks"].split("\n      - &install-rust\n", 1)[1].split("\n      - ", 1)[0]
        self.assertIn("id: install-rust\n", install)
        self.assertIn('echo "CARGO_TARGET_DIR=$RUNNER_TEMP/cargo-target" >> "$GITHUB_ENV"\n', install)
        # rust-cache joins the target onto the workspace root, so it gets the path from the project directory.
        self.assertIn('realpath -m --relative-to=. "$RUNNER_TEMP/cargo-target")" >> "$GITHUB_OUTPUT"\n', install)
        cache = jobs["checks"].split("\n      - &cache\n", 1)[1].split("\n      - ", 1)[0]
        self.assertIn(
            "workspaces: repo/${{ inputs.working-directory }} -> ${{ steps.install-rust.outputs.target }}\n", cache
        )
        for job in ("coverage", "coverage-shard"):
            steps = [step.split("\n", 1)[0] for step in jobs[job].split("\n      - ")[1:]]
            self.assertLess(steps.index("*install-rust"), steps.index("*cache"))
        # API snapshot installs its own toolchain; its generator builds the caller's crates.
        generate = jobs["api-snapshot"].split("\n      - name: Generate\n", 1)[1].split("\n      - ", 1)[0]
        self.assertIn("CARGO_TARGET_DIR: ${{ runner.temp }}/cargo-target\n", generate)


if __name__ == "__main__":
    unittest.main()
