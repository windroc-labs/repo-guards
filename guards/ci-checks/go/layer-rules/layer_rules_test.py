import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from layer_rules import CONFIG_NAMES, Error, depguard_config, find_config, load, main

HERE = Path(__file__).resolve().parent
LAYERS = HERE / "testdata/layers"
# The end-to-end cases run the real tool; the Checker tests job installs the pinned golangci-lint and sets this.
GOLANGCI_LINT = os.environ.get("GOLANGCI_LINT")

DOMAIN_RULE = {
    "rules": {
        "domain": {
            "files": ["${base-path}/domain/**"],
            "deny": [{"pkg": "example.com/layers/app", "desc": "domain must not import app"}],
        }
    }
}


def v2(**linters):
    return {"version": "2", "linters": linters}


def with_rule(**more):
    return v2(settings={"depguard": DOMAIN_RULE}, **more)


class Find(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_none(self):
        self.assertIsNone(find_config(self.dir))

    def test_viper_order_picks_the_first(self):
        # golangci-lint looks names up in viper's SupportedExts order: json, toml, yaml, yml.
        for name in reversed(CONFIG_NAMES):
            (self.dir / name).write_text("{}")
            self.assertEqual(find_config(self.dir), self.dir / name)
        self.assertEqual(CONFIG_NAMES, (".golangci.json", ".golangci.toml", ".golangci.yaml", ".golangci.yml"))

    def test_only_the_directory_itself(self):
        (self.dir / ".golangci.yml").write_text("version: '2'\n")
        sub = self.dir / "sub"
        sub.mkdir()
        (sub / "x").mkdir()
        (sub / "x/.golangci.yml").write_text("version: '2'\n")
        self.assertIsNone(find_config(sub))


class Load(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return path

    def test_each_format(self):
        want = {"version": "2", "linters": {"settings": {"depguard": {"rules": {"a": {"files": ["$all"]}}}}}}
        texts = {
            ".golangci.json": json.dumps(want),
            ".golangci.toml": 'version = "2"\n[linters.settings.depguard.rules.a]\nfiles = ["$all"]\n',
            ".golangci.yaml": "version: '2'\nlinters:\n  settings:\n    depguard:\n      rules:\n        a:\n          files: [$all]\n",
            ".golangci.yml": "version: '2'\nlinters:\n  settings:\n    depguard:\n      rules:\n        a:\n          files: [$all]\n",
        }
        for name, text in texts.items():
            with self.subTest(name):
                self.assertEqual(load(self.write(name, text)), want)

    def test_keys_ignore_case_like_viper(self):
        text = "Version: '2'\nLinters:\n  Settings:\n    DepGuard:\n      Rules:\n        Domain:\n          Files: [$all]\n          Deny: [{Pkg: example.com/A}]\n"
        config = load(self.write(".golangci.yml", text))
        self.assertEqual(config, {"version": "2", "linters": {"settings": {"depguard": {"rules": {"domain": {"files": ["$all"], "deny": [{"pkg": "example.com/A"}]}}}}}})
        self.assertIsNotNone(depguard_config(config, Path(".golangci.yml")))

    def test_yaml_scalars_read_as_yaml_1_2(self):
        # yaml.v3 keeps a date and yes/no as strings; YAML 1.1 would make them a date and booleans.
        text = "a: 2024-01-01\nb: no\nc: yes\nd: true\ne: False\nf: on\n"
        self.assertEqual(load(self.write(".golangci.yml", text)), {"a": "2024-01-01", "b": "no", "c": "yes", "d": True, "e": False, "f": "on"})

    def test_empty_yaml_is_an_empty_config(self):
        self.assertEqual(load(self.write(".golangci.yml", "")), {})

    def test_parse_errors(self):
        for name, text in {
            ".golangci.json": "{",
            ".golangci.toml": "version = ",
            ".golangci.yml": "linters: [\n",
        }.items():
            with self.subTest(name), self.assertRaisesRegex(Error, name):
                load(self.write(name, text))

    def test_not_a_mapping(self):
        with self.assertRaisesRegex(Error, "mapping"):
            load(self.write(".golangci.yml", "- a\n- b\n"))


class Generate(unittest.TestCase):
    def test_no_depguard_rules_is_no_config(self):
        for project in [
            {},
            {"version": "2"},
            v2(enable=["errcheck"]),
            v2(settings={"depguard": {}}),
            v2(settings={"depguard": {"rules": {}}}),
        ]:
            with self.subTest(project):
                self.assertIsNone(depguard_config(project, Path(".golangci.yml")))

    def test_a_section_that_is_not_a_mapping_is_an_error(self):
        # golangci-lint rejects these too.
        for project, where in [
            ({"version": "2", "linters": "x"}, "linters"),
            (v2(settings=["depguard"]), "linters.settings"),
            (v2(settings={"depguard": "x"}), "linters.settings.depguard"),
            ({**with_rule(), "run": 5}, "run"),
        ]:
            with self.subTest(project), self.assertRaisesRegex(Error, f"{where} is not a mapping"):
                depguard_config(project, Path(".golangci.yml"))

    def test_a_null_section_is_an_empty_one(self):
        # A YAML key whose children are all commented out is null; golangci-lint reads it as empty.
        self.assertIsNone(depguard_config(v2(settings={"depguard": None}), Path(".golangci.yml")))
        self.assertIsNone(depguard_config({"version": "2", "linters": None}, Path(".golangci.yml")))
        config = depguard_config({**with_rule(), "run": None}, Path(".golangci.yml"))
        self.assertEqual(config["run"], {"relative-path-mode": "wd"})

    def test_numeric_version_2_is_v2(self):
        for value in (2, 2.0, "2"):
            with self.subTest(value):
                self.assertIsNotNone(depguard_config({**with_rule(), "version": value}, Path(".golangci.yml")))

    def test_config_path_is_the_config_directory(self):
        rule = {"rules": {"app": {"files": ["${config-path}/app/**", "!${config-path}/app/x/**", "$test"], "deny": []}}}
        config = depguard_config(v2(settings={"depguard": rule}), Path("/p/.golangci.yml"))
        self.assertEqual(config["linters"]["settings"]["depguard"]["rules"]["app"]["files"], ["/p/app/**", "!/p/app/x/**", "$test"])
        self.assertEqual(rule["rules"]["app"]["files"][0], "${config-path}/app/**")

    def test_v1_with_depguard_is_an_error(self):
        for project in [
            {"linters-settings": {"depguard": {"rules": {"a": {}}}}},
            {"version": "1", "linters": {"settings": {"depguard": {"rules": {"a": {}}}}}},
        ]:
            with self.subTest(project), self.assertRaisesRegex(Error, 'version: "2"'):
                depguard_config(project, Path(".golangci.yml"))

    def test_v1_without_depguard_is_no_config(self):
        self.assertIsNone(depguard_config({"linters-settings": {"errcheck": {}}}, Path(".golangci.yml")))

    def test_only_depguard_settings_are_copied(self):
        project = {
            "version": "2",
            "run": {"timeout": "1s", "go": "1.20", "modules-download-mode": "mod"},
            "output": {"formats": {"json": {"path": "/elsewhere/out.json"}}},
            "issues": {"new": True, "new-from-rev": "HEAD", "max-issues-per-linter": 1},
            "formatters": {"enable": ["gofmt"]},
            "linters": {
                "default": "all",
                "enable": ["errcheck"],
                "disable": ["depguard"],
                "settings": {"depguard": DOMAIN_RULE, "custom": {"evil": {"path": "plugin.so"}}},
            },
        }
        self.assertEqual(
            depguard_config(project, Path(".golangci.yml")),
            {
                "version": "2",
                "run": {"relative-path-mode": "wd"},
                "linters": {"default": "none", "enable": ["depguard"], "settings": {"depguard": DOMAIN_RULE}},
                "issues": {"max-issues-per-linter": 0, "max-same-issues": 0},
            },
        )

    def test_exclusions_build_tags_and_tests_are_copied(self):
        exclusions = {"paths": ["domain/legacy"], "rules": [{"path": "x", "linters": ["depguard"]}]}
        project = with_rule(exclusions=exclusions)
        project["run"] = {"build-tags": ["integration"], "tests": False}
        config = depguard_config(project, Path(".golangci.yml"))
        self.assertEqual(config["linters"]["exclusions"], exclusions)
        self.assertEqual(config["run"], {"relative-path-mode": "wd", "build-tags": ["integration"], "tests": False})

    def test_relative_path_mode(self):
        # The config sits in the project directory, so cfg is wd; the generated config lives elsewhere.
        for mode, want in [(None, "wd"), ("", "wd"), ("cfg", "wd"), ("wd", "wd"), ("gomod", "gomod"), ("gitroot", "gitroot"), ("bad", "bad")]:
            with self.subTest(mode):
                project = with_rule()
                if mode is not None:
                    project["run"] = {"relative-path-mode": mode}
                self.assertEqual(depguard_config(project, Path(".golangci.yml"))["run"]["relative-path-mode"], want)


class Main(unittest.TestCase):
    """main() with a stand-in golangci-lint that records its arguments and the config it was given."""

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.record = self.dir / "record.json"
        self.fake = self.dir / "fake-golangci-lint"
        self.fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "config = json.load(open(sys.argv[sys.argv.index('--config') + 1]))\n"
            f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'config': config}}, open({str(self.record)!r}, 'w'))\n"
            "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n"
        )
        self.fake.chmod(0o755)
        self.project = self.dir / "project"
        self.project.mkdir()
        self.enterContext(contextlib.chdir(self.project))

    def run_main(self, exit_code=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), unittest.mock.patch.dict(os.environ, {"FAKE_EXIT": str(exit_code)}):
            code = main(["--golangci-lint", str(self.fake)])
        return code, out.getvalue(), err.getvalue()

    def test_no_config_skips_with_one_line(self):
        code, out, err = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1, out)
        for word in ("depguard", "golangci-lint", *CONFIG_NAMES):
            self.assertIn(word, out)
        self.assertFalse(self.record.exists())

    def test_config_without_rules_skips_with_one_line(self):
        (self.project / ".golangci.yml").write_text("version: '2'\nlinters:\n  enable: [errcheck]\n")
        code, out, err = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertIn(".golangci.yml", out)
        self.assertIn("depguard", out)
        self.assertFalse(self.record.exists())

    def test_rules_run_the_tool_on_the_whole_project(self):
        (self.project / ".golangci.json").write_text(json.dumps(with_rule()))
        code, out, err = self.run_main()
        self.assertEqual(code, 0)
        record = json.loads(self.record.read_text())
        self.assertEqual(record["argv"][0], "run")
        self.assertEqual(record["argv"][-1], "./...")
        self.assertEqual(Path(record["cwd"]).resolve(), self.project.resolve())
        self.assertEqual(record["config"], depguard_config(with_rule(), Path(".golangci.json")))
        self.assertIn(".golangci.json", out)

    def test_tool_exit_codes(self):
        (self.project / ".golangci.json").write_text(json.dumps(with_rule()))
        for tool, want in [(0, 0), (1, 1), (3, 2), (7, 2)]:
            with self.subTest(tool):
                self.assertEqual(self.run_main(tool)[0], want)

    def test_bad_config_exits_2(self):
        (self.project / ".golangci.yml").write_text("linters: [\n")
        code, out, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn(".golangci.yml", err)
        self.assertFalse(self.record.exists())

    def test_missing_tool_exits_2(self):
        (self.project / ".golangci.json").write_text(json.dumps(with_rule()))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--golangci-lint", str(self.dir / "missing")])
        self.assertEqual(code, 2)
        self.assertIn("missing", err.getvalue())


class Pin(unittest.TestCase):
    def test_tests_use_the_golangci_lint_go_yml_installs(self):
        pin = re.compile(r"golangci-lint/v2/cmd/golangci-lint@(v[0-9.]+)")
        workflows = HERE.parents[3] / ".github/workflows"
        versions = {name: pin.findall((workflows / name).read_text()) for name in ("go.yml", "ci.yml")}
        self.assertEqual(len(versions["go.yml"]), 1, versions)
        self.assertEqual(versions["ci.yml"], versions["go.yml"], versions)


@unittest.skipUnless(GOLANGCI_LINT, "GOLANGCI_LINT is not set")
class EndToEnd(unittest.TestCase):
    """The pinned golangci-lint on testdata/layers, where domain/legacy imports app."""

    def setUp(self):
        self.project = Path(self.enterContext(tempfile.TemporaryDirectory())) / "layers"
        shutil.copytree(LAYERS, self.project)

    def run_script(self, name, config):
        text = config if isinstance(config, str) else json.dumps(config)
        (self.project / name).write_text(text)
        return subprocess.run(
            [sys.executable, "-I", str(HERE / "layer_rules.py"), "--golangci-lint", GOLANGCI_LINT],
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_violation_fails(self):
        yaml = (
            'version: "2"\n'
            "linters:\n"
            "  settings:\n"
            "    depguard:\n"
            "      rules:\n"
            "        domain:\n"
            '          files: ["${base-path}/domain/**"]\n'
            "          deny:\n"
            "            - pkg: example.com/layers/app\n"
            "              desc: domain must not import app\n"
        )
        result = self.run_script(".golangci.yml", yaml)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("domain/legacy/legacy.go", result.stdout)
        self.assertIn("domain must not import app", result.stdout)

    def test_no_violation_passes(self):
        rule = {"rules": {"app": {"files": ["${base-path}/app/**"], "deny": [{"pkg": "example.com/layers/domain/legacy"}]}}}
        result = self.run_script(".golangci.json", v2(settings={"depguard": rule}))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_config_path_names_the_project(self):
        rule = {"rules": {"app": {"files": ["${config-path}/app/**"], "deny": [{"pkg": "example.com/layers/domain"}]}}}
        result = self.run_script(".golangci.json", v2(settings={"depguard": rule}))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("app/app.go", result.stdout)

    def test_files_negation_exempts(self):
        rule = {"rules": {"domain": {**DOMAIN_RULE["rules"]["domain"], "files": ["${base-path}/domain/**", "!**/legacy/**"]}}}
        result = self.run_script(".golangci.json", v2(settings={"depguard": rule}))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exclusions_exempt(self):
        result = self.run_script(".golangci.json", with_rule(exclusions={"paths": ["domain/legacy"]}))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_project_settings_outside_depguard_are_ignored(self):
        # A Go plugin path and other linters would fail or change the run if they reached golangci-lint.
        project = with_rule(enable=["errcheck"])
        project["linters"]["settings"]["custom"] = {"evil": {"path": "missing-plugin.so"}}
        project["issues"] = {"new-from-rev": "no-such-rev"}
        result = self.run_script(".golangci.json", project)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("domain/legacy/legacy.go", result.stdout)
        self.assertNotIn("errcheck", result.stdout)

    def test_rejected_rule_fails(self):
        project = with_rule()
        project["linters"]["settings"]["depguard"] = {"rules": {"domain": {"list-mode": "bogus", "files": ["$all"]}}}
        result = self.run_script(".golangci.json", project)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("bogus", result.stdout + result.stderr)

    def test_no_config_passes(self):
        result = subprocess.run(
            [sys.executable, "-I", str(HERE / "layer_rules.py"), "--golangci-lint", GOLANGCI_LINT],
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("depguard not run", result.stdout)


if __name__ == "__main__":
    unittest.main()
