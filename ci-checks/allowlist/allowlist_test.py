import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from allowlist import (
    Allowlist,
    Error,
    LicenseException,
    Locked,
    Spec,
    load,
    main,
    normalize_git,
    pep503,
    read_package_lock,
    read_requirements,
    read_uv_lock,
    violations,
)

PYPI = "https://pypi.org/simple"


def load_text(text: str, language: str = "rust") -> Allowlist:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "allowlist.toml")
        path.write_text(text)
        return load(path, language)


class Loading(unittest.TestCase):
    def test_absent_file_is_an_empty_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load(Path(tmp, "missing.toml"), "python"), Allowlist())

    def test_each_language_reads_only_its_own_entries(self):
        text = (
            '[bans]\nallow = ["serde@1.0.228"]\n'
            '[bans.build]\nallow-build-scripts = ["blake3"]\n'
            '[sources]\nallow-git = ["https://git.example.org/o/crate"]\n'
            '[python.bans.build]\nallow-build-scripts = ["somepkg"]\n'
            '[typescript.sources]\nallow-git = ["https://git.example.org/o/js"]\n'
        )
        rust, python, typescript = (
            load_text(text, lang) for lang in ("rust", "python", "typescript")
        )
        self.assertEqual(rust.packages, (Spec("serde", "1.0.228"),))
        self.assertEqual(rust.build, (Spec("blake3"),))
        self.assertNotIn("python", rust.raw)
        self.assertNotIn("typescript", rust.raw)
        self.assertEqual(python, Allowlist(build=(Spec("somepkg"),)))
        self.assertEqual(typescript, Allowlist(git=("https://git.example.org/o/js",)))

    def test_every_section_is_validated_whichever_language_loads(self):
        for text in (
            '[python.bans]\nallow = "x"\n',
            "[typescript.sources]\nallow-gti = []\n",
            'python = ["x"]\n',
        ):
            with self.assertRaisesRegex(Error, r"^(python|typescript)", msg=text):
                load_text(text, "rust")

    def test_every_kind_is_read(self):
        allowlist = load_text(
            '[bans]\nallow = ["serde@1.0.228", { name = "libc" }, { crate = "ring@0.17.8" }]\n'
            '[bans.build]\nallow-build-scripts = ["somepkg"]\n'
            '[sources]\nallow-git = ["https://github.com/example/repo"]\nallow-registry = ["https://example.org/simple"]\n'
            '[[licenses.exceptions]]\ncrate = "ring"\nallow = ["OpenSSL"]\n'
            '[[licenses.exceptions]]\nname = "other"\nversion = "2.0.0"\nallow = ["MPL-2.0"]\n'
        )
        self.assertEqual(
            allowlist.packages, (Spec("serde", "1.0.228"), Spec("libc"), Spec("ring", "0.17.8"))
        )
        self.assertEqual(allowlist.build, (Spec("somepkg"),))
        self.assertEqual(allowlist.git, ("https://github.com/example/repo",))
        self.assertEqual(allowlist.registries, ("https://example.org/simple",))
        self.assertEqual(
            allowlist.licenses,
            (
                LicenseException(Spec("ring"), ("OpenSSL",)),
                LicenseException(Spec("other", "2.0.0"), ("MPL-2.0",)),
            ),
        )
        self.assertEqual(allowlist.raw["bans"]["build"]["allow-build-scripts"], ["somepkg"])

    def test_star_version_means_any_version(self):
        self.assertEqual(
            load_text('[bans]\nallow = ["six@*", { name = "idna", version = "*" }]\n').packages,
            (Spec("six"), Spec("idna")),
        )

    def test_scoped_npm_name_keeps_its_at_sign(self):
        self.assertEqual(
            load_text('[bans]\nallow = ["@scope/pkg@1.2.3", "@scope/other"]\n').packages,
            (Spec("@scope/pkg", "1.2.3"), Spec("@scope/other")),
        )

    def test_rule_keys_are_rejected(self):
        for text in (
            '[licenses]\nallow = ["GPL-3.0"]\n',
            '[sources]\nunknown-git = "allow"\n',
            '[advisories]\nignore = ["RUSTSEC-2020-0001"]\n',
        ):
            with self.assertRaisesRegex(Error, "not an allowlist key"):
                load_text(text)

    def test_allowlist_key_must_be_an_array(self):
        with self.assertRaisesRegex(Error, "bans.allow: must be an array"):
            load_text('[bans]\nallow = "serde"\n')

    def test_malformed_entries_are_rejected(self):
        for text in (
            "[bans]\nallow = [1]\n",
            '[bans]\nallow = [{ reason = "x" }]\n',
            '[bans]\nallow = [""]\n',
            '[sources]\nallow-git = [{ url = "x" }]\n',
            '[[licenses.exceptions]]\ncrate = "ring"\n',
            '[[licenses.exceptions]]\nallow = ["MIT"]\n',
            '[licenses]\nexceptions = ["ring"]\n',
        ):
            with self.assertRaises(Error, msg=text):
                load_text(text)

    def test_bad_toml_is_an_error(self):
        with self.assertRaises(Error):
            load_text("[bans\n")


class Specs(unittest.TestCase):
    def test_name_only_matches_every_version(self):
        self.assertTrue(Spec("six").matches("six", "1.17.0"))

    def test_version_must_match_when_given(self):
        self.assertTrue(Spec("six", "1.17.0").matches("six", "1.17.0"))
        self.assertFalse(Spec("six", "1.16.0").matches("six", "1.17.0"))

    def test_names_match_after_pep_503_normalization(self):
        self.assertTrue(Spec("Typing_Extensions").matches("typing-extensions", "4.0.0", pep503))
        self.assertFalse(Spec("Typing_Extensions").matches("typing-extensions", "4.0.0"))
        self.assertFalse(Spec("typing").matches("typing-extensions", "4.0.0", pep503))


class GitUrls(unittest.TestCase):
    def test_forms_of_one_repo_normalize_alike(self):
        for url in (
            "https://git.example.org/Example/Repo",
            "https://git.example.org/Example/Repo/",
            "https://Git.Example.org/Example/Repo.git",
            "git+https://git.example.org/Example/Repo?rev=abc#0123",
            "git+ssh://git@git.example.org/Example/Repo.git#0123",
            "ssh://git@git.example.org/Example/Repo",
            "git@git.example.org:Example/Repo.git",
            "https://git.example.org/Example/Repo@0123abc",
        ):
            self.assertEqual(normalize_git(url), "git.example.org/Example/Repo", url)

    def test_different_repos_differ(self):
        self.assertNotEqual(
            normalize_git("https://git.example.org/a/repo"),
            normalize_git("https://git.example.org/b/repo"),
        )


class Violations(unittest.TestCase):
    def check(self, locked: list[Locked], allowlist: Allowlist | None = None) -> list[str]:
        return violations(locked, allowlist or Allowlist(), "python.")

    def test_registries_and_local_packages_have_no_rule(self):
        self.assertEqual(
            self.check(
                [
                    Locked("six", "1.17.0", "registry", PYPI),
                    Locked("mirrored", "1.0", "registry", "https://mirror.example.org/simple"),
                    Locked("me", "0", "local", ""),
                ]
            ),
            [],
        )

    def test_remote_tarball_needs_a_prefix_entry_naming_its_origin(self):
        locked = [Locked("pkg", "", "remote", "https://files.example.org/pkg/pkg-1.0.0.tgz")]
        (message,) = violations(locked, Allowlist(), "typescript.")
        self.assertIn("add https://files.example.org to typescript.sources.allow-registry", message)
        self.assertEqual(
            violations(locked, Allowlist(registries=("https://files.example.org",)), "typescript."),
            [],
        )
        self.assertEqual(
            len(
                violations(locked, Allowlist(registries=("https://files.example",)), "typescript.")
            ),
            1,
        )

    def test_git_source_needs_an_entry(self):
        locked = [
            Locked("somepkg", "1.0.0", "git", "git+https://github.com/example/somepkg?rev=abc#abc")
        ]
        (message,) = self.check(locked)
        self.assertIn("python.sources.allow-git", message)
        self.assertIn("https://github.com/example/somepkg", message)
        self.assertEqual(
            self.check(locked, Allowlist(git=("https://github.com/example/somepkg.git",))), []
        )

    def test_empty_package_list_restricts_nothing(self):
        self.assertEqual(self.check([Locked("six", "1.17.0", "registry", PYPI)]), [])

    def test_package_list_restricts_when_set(self):
        allowlist = Allowlist(packages=(Spec("six", "1.17.0"),))
        locked = [
            Locked("six", "1.17.0", "registry", PYPI),
            Locked("idna", "3.10", "registry", PYPI),
            Locked("me", "0", "local", ""),
        ]
        (message,) = self.check(locked, allowlist)
        self.assertIn("idna@3.10", message)
        self.assertIn("python.bans.allow", message)

    def test_url_and_local_sources_have_no_rule(self):
        self.assertEqual(
            self.check([Locked("w", "1.0", "url", "https://example.org/w-1.0-py3-none-any.whl")]),
            [],
        )


UV_LOCK = """\
version = 1
requires-python = ">=3.12"

[[package]]
name = "demo"
version = "0.1.0"
source = { virtual = "." }

[[package]]
name = "six"
version = "1.17.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "private-pkg"
version = "2.0.0"
source = { registry = "https://pypi.example.org/simple" }

[[package]]
name = "somepkg"
version = "4.0.0"
source = { git = "https://github.com/example/somepkg?rev=0123#0123" }

[[package]]
name = "member"
version = "0.2.0"
source = { editable = "packages/member" }

[[package]]
name = "wheel-url"
version = "1.0"
source = { url = "https://example.org/wheel_url-1.0-py3-none-any.whl" }

[[package]]
name = "numpy"
version = "2.0.2"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["python_full_version < '3.10'"]

[[package]]
name = "numpy"
version = "2.2.6"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["python_full_version >= '3.11'", "python_full_version == '3.10.*'"]
"""

REQUIREMENTS = """\
# This file was autogenerated by uv via the following command:
#    uv pip compile --emit-index-annotation --generate-hashes requirements.txt
-e ./packages/member
    # via -r requirements.txt
peppercorn==0.6 \\
    --hash=sha256:aaaa \\
    --hash=sha256:bbbb
    # via somepkg
    # from https://pypi.org/simple
private-pkg==2.0.0 ; python_full_version >= '3.12' \\
    --hash=sha256:cccc
    # via -r requirements.txt
    # from https://pypi.example.org/simple
somepkg @ git+https://github.com/example/somepkg@0123
    # via -r requirements.txt
wheel-url @ https://example.org/wheel_url-1.0-py3-none-any.whl
    # via -r requirements.txt
local-pkg @ file:///work/local_pkg
    # via -r requirements.txt
"""


class Readers(unittest.TestCase):
    def write(self, tmp: str, name: str, text: str) -> Path:
        path = Path(tmp, name)
        path.write_text(text)
        return path

    def test_uv_lock_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked = read_uv_lock(self.write(tmp, "uv.lock", UV_LOCK))
        self.assertEqual(
            locked,
            [
                Locked("demo", "0.1.0", "local", ""),
                Locked("six", "1.17.0", "registry", "https://pypi.org/simple"),
                Locked("private-pkg", "2.0.0", "registry", "https://pypi.example.org/simple"),
                Locked(
                    "somepkg", "4.0.0", "git", "https://github.com/example/somepkg?rev=0123#0123"
                ),
                Locked("member", "0.2.0", "local", ""),
                Locked(
                    "wheel-url", "1.0", "url", "https://example.org/wheel_url-1.0-py3-none-any.whl"
                ),
                Locked("numpy", "2.0.2", "registry", PYPI),
                Locked("numpy", "2.2.6", "registry", PYPI),
            ],
        )

    def test_compiled_requirements_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked = read_requirements(self.write(tmp, "requirements.txt", REQUIREMENTS))
        self.assertEqual(
            locked,
            [
                Locked("member", "", "local", ""),
                Locked("peppercorn", "0.6", "registry", "https://pypi.org/simple"),
                Locked("private-pkg", "2.0.0", "registry", "https://pypi.example.org/simple"),
                Locked("somepkg", "", "git", "git+https://github.com/example/somepkg@0123"),
                Locked(
                    "wheel-url", "", "url", "https://example.org/wheel_url-1.0-py3-none-any.whl"
                ),
                Locked("local-pkg", "", "local", ""),
            ],
        )

    def test_uv_lock_unknown_or_missing_source_is_an_error(self):
        for source in ('source = { mystery = "x" }\n', ""):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                path = self.write(
                    tmp, "uv.lock", f'[[package]]\nname = "odd"\nversion = "1"\n{source}'
                )
                with self.assertRaisesRegex(Error, "odd"):
                    read_uv_lock(path)

    def test_unknown_option_line_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "r.txt", "--trusted-host example.org\nsix==1.17.0\n")
            with self.assertRaisesRegex(Error, "--trusted-host"):
                read_requirements(path)

    def test_index_and_build_option_lines_are_read_past(self):
        text = (
            "--index-url https://pypi.org/simple\n--extra-index-url https://x.example/simple\n"
            "--find-links https://x.example/f\n--no-index\n--no-binary somepkg\n"
            "--only-binary :all:\nsix==1.17.0\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            locked = read_requirements(self.write(tmp, "r.txt", text))
        self.assertEqual(locked, [Locked("six", "1.17.0", "registry", "")])

    def test_registry_package_without_an_index_annotation_has_no_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked = read_requirements(self.write(tmp, "r.txt", "six==1.17.0\n"))
        self.assertEqual(locked, [Locked("six", "1.17.0", "registry", "")])


class Cli(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_check_python_reports_each_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp, "uv.lock")
            lock.write_text(UV_LOCK)
            code, _, err = self.run_main(
                [
                    "check",
                    "python",
                    "--allowlist",
                    str(Path(tmp, "none.toml")),
                    "--uv-lock",
                    str(lock),
                ]
            )
            self.assertEqual(code, 1)
            self.assertNotIn("private-pkg", err)
            self.assertIn("somepkg@4.0.0: git source", err)
            self.assertIn(
                "somepkg@4.0.0: a git dependency builds from source; "
                "add somepkg to python.bans.build.allow-build-scripts",
                err,
            )

    def test_check_python_declared_reads_git_lines_before_resolving(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "requirements.txt").write_text(
                "-r more.txt\n"
                "six==1.17.0\n"
                "somepkg @ git+https://git.example.org/o/somepkg@0123  # pinned\n"
                "-e git+https://git.example.org/o/editable.git#egg=editable\n"
            )
            Path(tmp, "more.txt").write_text(
                "other[extra] @ git+https://git.example.org/o/other@1\n"
            )
            Path(tmp, "a.toml").write_text(
                '[python.sources]\nallow-git = ["https://git.example.org/o/somepkg", "https://git.example.org/o/other"]\n'
                '[python.bans.build]\nallow-build-scripts = ["somepkg", "other"]\n'
            )
            code, _, err = self.run_main(
                [
                    "check",
                    "python",
                    "--allowlist",
                    str(Path(tmp, "a.toml")),
                    "--declared",
                    str(Path(tmp, "requirements.txt")),
                    str(Path(tmp, "requirements-dev.txt")),
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(
            [line.split(":")[0] for line in err.splitlines()[:-1]], ["editable", "editable"]
        )
        self.assertIn("add https://git.example.org/o/editable to python.sources.allow-git", err)

    def test_check_python_ignores_other_languages_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.toml").write_text('[bans]\nallow = ["serde@1.0.228"]\n')
            Path(tmp, "r.txt").write_text("six==1.17.0\n    # from https://pypi.org/simple\n")
            code, _, err = self.run_main(
                [
                    "check",
                    "python",
                    "--allowlist",
                    str(Path(tmp, "a.toml")),
                    "--requirements",
                    str(Path(tmp, "r.txt")),
                ]
            )
        self.assertEqual((code, err), (0, ""))

    def test_check_python_passes_with_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.toml").write_text(
                '[python.bans.build]\nallow-build-scripts = ["SomePkg"]\n'
                '[python.sources]\nallow-git = ["https://github.com/example/somepkg"]\n'
            )
            Path(tmp, "r.txt").write_text(REQUIREMENTS)
            code, _, err = self.run_main(
                [
                    "check",
                    "python",
                    "--allowlist",
                    str(Path(tmp, "a.toml")),
                    "--requirements",
                    str(Path(tmp, "r.txt")),
                ]
            )
        self.assertEqual((code, err), (0, ""))

    def test_check_python_needs_exactly_one_lock(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main(["check", "python", "--allowlist", "a.toml"])

    def test_bad_allowlist_or_lock_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "bad.toml").write_text("[bans]\nallow = 1\n")
            Path(tmp, "uv.lock").write_text(UV_LOCK)
            code, _, err = self.run_main(
                [
                    "check",
                    "python",
                    "--allowlist",
                    str(Path(tmp, "bad.toml")),
                    "--uv-lock",
                    str(Path(tmp, "uv.lock")),
                ]
            )
            self.assertEqual(code, 2)
            self.assertIn("allowlist: bans.allow", err)
            code, _, _ = self.run_main(
                [
                    "check",
                    "python",
                    "--allowlist",
                    str(Path(tmp, "x.toml")),
                    "--uv-lock",
                    str(Path(tmp, "missing.lock")),
                ]
            )
            self.assertEqual(code, 2)

    def test_env_python_lists_build_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.toml").write_text(
                '[bans.build]\nallow-build-scripts = ["blake3"]\n'
                '[python.bans.build]\nallow-build-scripts = ["somepkg", "other@1.0"]\n'
                '[typescript.bans.build]\nallow-build-scripts = ["@parcel/watcher"]\n'
            )
            self.assertEqual(
                self.run_main(["env", "python", "--allowlist", str(Path(tmp, "a.toml"))]),
                (0, "UV_NO_BINARY_PACKAGE=somepkg other\n", ""),
            )
            Path(tmp, "bad.toml").write_text(
                '[python.bans.build]\nallow-build-scripts = ["@parcel/watcher"]\n'
            )
            code, out, err = self.run_main(
                ["env", "python", "--allowlist", str(Path(tmp, "bad.toml"))]
            )
            self.assertEqual((code, out), (2, ""))
            self.assertIn("not a Python package name", err)
            self.assertEqual(
                self.run_main(["env", "python", "--allowlist", str(Path(tmp, "none.toml"))]),
                (0, "", ""),
            )


PACKAGE_LOCK = {
    "name": "app",
    "lockfileVersion": 3,
    "packages": {
        "": {
            "name": "app",
            "version": "1.0.0",
            "workspaces": ["packages/*"],
            "dependencies": {
                "left-pad": "^1.3.0",
                "tarred": "https://files.example.org/tarred-1.0.0.tgz",
                "hosted": "https://github.com/o/hosted",
                "overridden": "^4.0.0",
                "regurl": "https://registry.npmjs.org/regurl/-/regurl-1.0.0.tgz",
            },
        },
        "node_modules/hosted": {
            "version": "1.0.0",
            "resolved": "git+https://github.com/o/hosted.git#0123",
        },
        "node_modules/overridden": {
            "version": "4.0.0",
            "resolved": "https://files.example.org/overridden.tgz",
        },
        "node_modules/regurl": {
            "version": "1.0.0",
            "resolved": "https://registry.npmjs.org/regurl/-/regurl-1.0.0.tgz",
        },
        "node_modules/tarred": {
            "version": "1.0.0",
            "resolved": "https://files.example.org/tarred-1.0.0.tgz",
        },
        "node_modules/left-pad": {
            "version": "1.3.0",
            "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
        },
        "node_modules/@scope/private": {
            "version": "2.0.0",
            "resolved": "https://npm.example.org/@scope/private/-/private-2.0.0.tgz",
        },
        "node_modules/somepkg": {
            "version": "4.0.0",
            "resolved": "git+ssh://git@git.example.org/example/somepkg.git#0123",
        },
        "node_modules/alias": {
            "name": "real-name",
            "version": "1.0.0",
            "resolved": "https://registry.npmjs.org/real-name/-/real-name-1.0.0.tgz",
        },
        "node_modules/left-pad/node_modules/nested": {
            "version": "0.1.0",
            "resolved": "https://registry.npmjs.org/nested/-/nested-0.1.0.tgz",
        },
        "node_modules/member": {"resolved": "packages/member", "link": True},
        "packages/member": {"name": "member", "version": "0.1.0"},
        "node_modules/vendored": {"version": "1.0.0", "resolved": "file:vendor/vendored-1.0.0.tgz"},
        "node_modules/bundled": {"version": "1.0.0", "inBundle": True},
        "node_modules/omitted": {"version": "2.0.0", "integrity": "sha512-x"},
    },
}


class Npm(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_package_lock_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp, "package-lock.json")
            lock.write_text(json.dumps(PACKAGE_LOCK))
            locked = read_package_lock(lock)
        self.assertEqual(
            locked,
            [
                Locked("hosted", "1.0.0", "git", "git+https://github.com/o/hosted.git#0123"),
                Locked("overridden", "4.0.0", "remote", "https://files.example.org/overridden.tgz"),
                Locked(
                    "regurl",
                    "1.0.0",
                    "remote",
                    "https://registry.npmjs.org/regurl/-/regurl-1.0.0.tgz",
                ),
                Locked("tarred", "1.0.0", "remote", "https://files.example.org/tarred-1.0.0.tgz"),
                Locked(
                    "left-pad",
                    "1.3.0",
                    "registry",
                    "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                ),
                Locked(
                    "@scope/private",
                    "2.0.0",
                    "registry",
                    "https://npm.example.org/@scope/private/-/private-2.0.0.tgz",
                ),
                Locked(
                    "somepkg",
                    "4.0.0",
                    "git",
                    "git+ssh://git@git.example.org/example/somepkg.git#0123",
                ),
                Locked(
                    "real-name",
                    "1.0.0",
                    "registry",
                    "https://registry.npmjs.org/real-name/-/real-name-1.0.0.tgz",
                ),
                Locked(
                    "nested",
                    "0.1.0",
                    "registry",
                    "https://registry.npmjs.org/nested/-/nested-0.1.0.tgz",
                ),
                Locked("member", "", "local", ""),
                Locked("member", "0.1.0", "local", ""),
                Locked("vendored", "1.0.0", "local", ""),
                Locked("bundled", "1.0.0", "registry", ""),
                Locked("omitted", "2.0.0", "registry", ""),
            ],
        )

    def test_entry_without_resolved_is_held_to_the_package_list(self):
        lock = {"lockfileVersion": 3, "packages": {"node_modules/omitted": {"version": "2.0.0"}}}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "package-lock.json").write_text(json.dumps(lock))
            Path(tmp, "a.toml").write_text('[typescript.bans]\nallow = ["left-pad"]\n')
            code, _, err = self.run_main(
                [
                    "check",
                    "typescript",
                    "--allowlist",
                    str(Path(tmp, "a.toml")),
                    "--lock",
                    str(Path(tmp, "package-lock.json")),
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn('omitted@2.0.0: not in typescript.bans.allow; add "omitted@2.0.0"', err)

    def test_unknown_resolved_form_is_an_error(self):
        lock = {
            "lockfileVersion": 3,
            "packages": {"node_modules/odd": {"version": "1.0.0", "resolved": "ftp://x/odd.tgz"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "package-lock.json")
            path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(Error, "node_modules/odd"):
                read_package_lock(path)

    def test_lockfile_v1_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp, "package-lock.json")
            lock.write_text(json.dumps({"lockfileVersion": 1, "dependencies": {}}))
            code, _, err = self.run_main(
                [
                    "check",
                    "typescript",
                    "--allowlist",
                    str(Path(tmp, "a.toml")),
                    "--lock",
                    str(lock),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("lockfileVersion", err)

    def test_check_npm(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp, "package-lock.json")
            lock.write_text(json.dumps(PACKAGE_LOCK))
            code, _, err = self.run_main(
                [
                    "check",
                    "typescript",
                    "--allowlist",
                    str(Path(tmp, "a.toml")),
                    "--lock",
                    str(lock),
                ]
            )
            self.assertEqual(code, 1)
            self.assertNotIn("@scope/private", err)
            self.assertIn(
                "tarred@1.0.0: tarball URL https://files.example.org/tarred-1.0.0.tgz", err
            )
            self.assertIn("overridden@4.0.0: tarball URL", err)
            self.assertIn("hosted@1.0.0: git source", err)
            self.assertNotIn("github.com to typescript.sources.allow-registry", err)
            self.assertIn("somepkg@4.0.0: git source", err)
            self.assertIn(
                "add https://git.example.org/example/somepkg to typescript.sources.allow-git", err
            )
            Path(tmp, "a.toml").write_text(
                '[typescript.sources]\nallow-git = ["https://git.example.org/example/somepkg", "https://github.com/o/hosted"]\n'
                'allow-registry = ["https://files.example.org", "https://registry.npmjs.org/regurl/"]\n'
            )
            code, _, err = self.run_main(
                [
                    "check",
                    "typescript",
                    "--allowlist",
                    str(Path(tmp, "a.toml")),
                    "--lock",
                    str(lock),
                ]
            )
        self.assertEqual((code, err), (0, ""))

    def test_env_typescript(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.toml").write_text(
                '[sources]\nallow-git = ["https://git.example.org/o/crate"]\n'
                '[typescript.bans.build]\nallow-build-scripts = ["esbuild", "@scope/native@1.2.3"]\n'
                '[typescript.sources]\nallow-git = ["https://github.com/example/somepkg"]\n'
                'allow-registry = ["https://files.example.org"]\n'
            )
            self.assertEqual(
                self.run_main(["env", "typescript", "--allowlist", str(Path(tmp, "a.toml"))]),
                (
                    0,
                    (
                        "npm_config_allow_git=all\nnpm_config_allow_remote=all\n"
                        "ALLOW_BUILD=esbuild @scope/native@1.2.3\n"
                    ),
                    "",
                ),
            )
            Path(tmp, "rust.toml").write_text(
                '[sources]\nallow-git = ["https://git.example.org/o/crate"]\n'
            )
            for name in ("none.toml", "rust.toml"):
                self.assertEqual(
                    self.run_main(["env", "typescript", "--allowlist", str(Path(tmp, name))]),
                    (0, "", ""),
                )

    def test_licenses_lists_each_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.toml").write_text(
                '[[licenses.exceptions]]\ncrate = "ring"\nallow = ["OpenSSL"]\n'
                '[[typescript.licenses.exceptions]]\ncrate = "pkg"\nallow = ["MPL-2.0", "Unlicense"]\n'
                '[[typescript.licenses.exceptions]]\nname = "@scope/other"\nversion = "1.0.0"\nallow = ["CC0-1.0"]\n'
            )
            self.assertEqual(
                self.run_main(["licenses", "typescript", "--allowlist", str(Path(tmp, "a.toml"))]),
                (0, "pkg\tMPL-2.0;Unlicense\n@scope/other@1.0.0\tCC0-1.0\n", ""),
            )


if __name__ == "__main__":
    unittest.main()
