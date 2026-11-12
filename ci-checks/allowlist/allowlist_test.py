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


def load_text(text: str) -> Allowlist:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "allowlist.toml")
        path.write_text(text)
        return load(path)


class Loading(unittest.TestCase):
    def test_absent_file_is_an_empty_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load(Path(tmp, "missing.toml")), Allowlist())

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
        return violations(locked, allowlist or Allowlist(), PYPI)

    def test_default_registry_and_local_packages_pass(self):
        self.assertEqual(
            self.check(
                [Locked("six", "1.17.0", "registry", PYPI + "/"), Locked("me", "0", "local", "")]
            ),
            [],
        )

    def test_other_registry_needs_an_entry(self):
        locked = [Locked("six", "1.17.0", "registry", "https://example.org/simple")]
        (message,) = self.check(locked)
        self.assertIn("sources.allow-registry", message)
        self.assertIn("https://example.org/simple", message)
        self.assertEqual(
            self.check(locked, Allowlist(registries=("https://example.org/simple/",))), []
        )

    def test_registry_entry_matches_a_url_prefix(self):
        locked = [Locked("pkg", "1.0.0", "registry", "https://npm.example.org/pkg/-/pkg-1.0.0.tgz")]
        self.assertEqual(
            violations(
                locked,
                Allowlist(registries=("https://npm.example.org",)),
                "https://registry.npmjs.org",
            ),
            [],
        )
        self.assertEqual(
            len(
                violations(
                    locked,
                    Allowlist(registries=("https://npm.example",)),
                    "https://registry.npmjs.org",
                )
            ),
            1,
        )

    def test_git_source_needs_an_entry(self):
        locked = [
            Locked("somepkg", "1.0.0", "git", "git+https://github.com/example/somepkg?rev=abc#abc")
        ]
        (message,) = self.check(locked)
        self.assertIn("sources.allow-git", message)
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
        self.assertIn("bans.allow", message)

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

    def test_check_python_reports_each_violation_and_writes_registry_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp, "uv.lock")
            lock.write_text(UV_LOCK)
            pinned = Path(tmp, "pinned.txt")
            code, _, err = self.run_main(
                [
                    "check",
                    "python",
                    "--allowlist",
                    str(Path(tmp, "none.toml")),
                    "--uv-lock",
                    str(lock),
                    "--pinned-out",
                    str(pinned),
                ]
            )
            self.assertEqual(code, 1)
            self.assertIn("private-pkg@2.0.0: registry https://pypi.example.org/simple", err)
            self.assertIn("somepkg@4.0.0: git source", err)
            self.assertIn(
                "somepkg@4.0.0: a git dependency builds from source; add somepkg to bans.build.allow-build-scripts",
                err,
            )
            self.assertEqual(pinned.read_text(), "six==1.17.0\nprivate-pkg==2.0.0\n")

    def test_check_python_passes_with_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.toml").write_text(
                '[bans.build]\nallow-build-scripts = ["SomePkg"]\n'
                '[sources]\nallow-git = ["https://github.com/example/somepkg"]\n'
                'allow-registry = ["https://pypi.example.org/simple"]\n'
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
                '[bans.build]\nallow-build-scripts = ["somepkg", "other@1.0"]\n'
            )
            self.assertEqual(
                self.run_main(["env", "python", "--allowlist", str(Path(tmp, "a.toml"))]),
                (0, "UV_NO_BINARY_PACKAGE=somepkg other\n", ""),
            )
            self.assertEqual(
                self.run_main(["env", "python", "--allowlist", str(Path(tmp, "none.toml"))]),
                (0, "", ""),
            )


PACKAGE_LOCK = {
    "name": "app",
    "lockfileVersion": 3,
    "packages": {
        "": {"name": "app", "version": "1.0.0", "workspaces": ["packages/*"]},
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
                Locked("bundled", "1.0.0", "local", ""),
            ],
        )

    def test_lockfile_v1_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp, "package-lock.json")
            lock.write_text(json.dumps({"lockfileVersion": 1, "dependencies": {}}))
            code, _, err = self.run_main(
                ["check", "npm", "--allowlist", str(Path(tmp, "a.toml")), "--lock", str(lock)]
            )
        self.assertEqual(code, 2)
        self.assertIn("lockfileVersion", err)

    def test_check_npm(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp, "package-lock.json")
            lock.write_text(json.dumps(PACKAGE_LOCK))
            code, _, err = self.run_main(
                ["check", "npm", "--allowlist", str(Path(tmp, "a.toml")), "--lock", str(lock)]
            )
            self.assertEqual(code, 1)
            self.assertIn("@scope/private@2.0.0: registry https://npm.example.org/", err)
            self.assertIn("somepkg@4.0.0: git source", err)
            self.assertIn("add https://git.example.org/example/somepkg to sources.allow-git", err)
            Path(tmp, "a.toml").write_text(
                '[sources]\nallow-git = ["https://git.example.org/example/somepkg"]\nallow-registry = ["https://npm.example.org"]\n'
            )
            code, _, err = self.run_main(
                ["check", "npm", "--allowlist", str(Path(tmp, "a.toml")), "--lock", str(lock)]
            )
        self.assertEqual((code, err), (0, ""))

    def test_env_npm(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.toml").write_text(
                '[bans.build]\nallow-build-scripts = ["esbuild", "@scope/native@1.2.3"]\n'
                '[sources]\nallow-git = ["https://github.com/example/somepkg"]\n'
            )
            self.assertEqual(
                self.run_main(["env", "npm", "--allowlist", str(Path(tmp, "a.toml"))]),
                (0, "npm_config_allow_git=all\nALLOW_BUILD=esbuild @scope/native@1.2.3\n", ""),
            )
            self.assertEqual(
                self.run_main(["env", "npm", "--allowlist", str(Path(tmp, "none.toml"))]),
                (0, "", ""),
            )

    def test_licenses_lists_each_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.toml").write_text(
                '[[licenses.exceptions]]\ncrate = "pkg"\nallow = ["MPL-2.0", "Unlicense"]\n'
                '[[licenses.exceptions]]\nname = "@scope/other"\nversion = "1.0.0"\nallow = ["CC0-1.0"]\n'
            )
            self.assertEqual(
                self.run_main(["licenses", "--allowlist", str(Path(tmp, "a.toml"))]),
                (0, "pkg\tMPL-2.0;Unlicense\n@scope/other@1.0.0\tCC0-1.0\n", ""),
            )


if __name__ == "__main__":
    unittest.main()
