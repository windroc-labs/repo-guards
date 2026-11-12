import tempfile
import unittest
from pathlib import Path

from allowlist import (
    Allowlist,
    Error,
    LicenseException,
    Locked,
    Spec,
    load,
    normalize_git,
    pep503,
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


if __name__ == "__main__":
    unittest.main()
