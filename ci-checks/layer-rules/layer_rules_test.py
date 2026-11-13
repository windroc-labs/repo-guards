import dataclasses
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import layer_rules as lr

FILES = frozenset(
    {
        "src/api/__init__.py",
        "src/api/a.py",
        "src/core/__init__.py",
        "src/core/inner.py",
        "src/core/sub/deep.py",
        "src/store/__init__.py",
        "src/store/inner.py",
        "src/util.py",
        "tests/test_api.py",
        "web/index.ts",
        "web/inner.ts",
        "core/README.md",
        "pkg/go/a.go",
    }
)


def layer(name, paths, exit):
    return {"name": name, "paths": paths, "exit": exit}


API = layer("api", ["src/api/**"], "src/api")
CORE = layer("core", ["src/core/**"], "src/core")
STORE = layer("store", ["src/store/**"], "src/store")


def overrides(*layers, wd=".", **section):
    return {"project": {wd: {"layers": list(layers), **section}}}


class Glob(unittest.TestCase):
    def test_double_star_crosses_directories_single_star_does_not(self):
        self.assertTrue(lr.matches(("src/**",), "src/a/b.py"))
        self.assertTrue(lr.matches(("**/test_*.py",), "test_x.py"))
        self.assertTrue(lr.matches(("**/test_*.py",), "a/b/test_x.py"))
        self.assertTrue(lr.matches(("src/*.py",), "src/a.py"))
        self.assertFalse(lr.matches(("src/*.py",), "src/a/b.py"))
        self.assertTrue(lr.matches(("src/**/x.py",), "src/x.py"))
        self.assertFalse(lr.matches(("src/a.py",), "src/a_py"))


class LoadConfig(unittest.TestCase):
    def load(self, data, language="python", wd=".", files=FILES):
        return lr.load_config(data, wd, language, files)

    def problems(self, data, **kwargs):
        with self.assertRaises(lr.ConfigError) as caught:
            self.load(data, **kwargs)
        return "\n".join(caught.exception.problems)

    def test_valid_layers_in_listed_order(self):
        config = self.load(overrides(API, CORE, STORE, **{"test-paths": ["tests/**"], "source-dirs": ["src", "tests"]}))
        self.assertEqual([l.name for l in config.layers], ["api", "core", "store"])
        self.assertEqual(config.layers[1].exit_file, "src/core/__init__.py")
        self.assertEqual(config.layer_of("src/core/sub/deep.py"), 1)
        self.assertIsNone(config.layer_of("src/util.py"))
        self.assertTrue(config.is_test("tests/test_api.py"))

    def test_module_file_exit(self):
        config = self.load(overrides(API, layer("core", ["src/core/**"], "src/core/inner.py")))
        self.assertEqual(config.layers[1].exit_file, "src/core/inner.py")

    def test_no_layers_or_no_layers_of_this_language(self):
        self.assertIsNone(self.load({}))
        self.assertIsNone(self.load({"project": {"app": {"layers": [API]}}}))
        ts = overrides(layer("ui", ["web/**"], "web/index.ts"))
        self.assertIsNone(self.load(ts))
        self.assertEqual(self.load(ts, language="typescript").layers[0].exit_file, "web/index.ts")
        self.assertEqual(lr.layered_projects({"project": {"app": {"layers": []}, ".": {}}}), ["app"])

    def test_each_language_takes_only_its_layers(self):
        data = overrides(layer("ui", ["web/index.ts"], "web/index.ts"), API, layer("client", ["web/inner.ts"], "web/inner.ts"), CORE)
        self.assertEqual([l.name for l in self.load(data).layers], ["api", "core"])
        self.assertEqual([l.name for l in self.load(data, language="typescript").layers], ["ui", "client"])

    def test_working_directory_selects_the_section(self):
        files = frozenset({"src/api/a.py", "src/core/__init__.py"})
        data = {"project": {"svc": {"layers": [layer("api", ["src/api/**"], "src/api/a.py"), CORE]}}}
        self.assertEqual(len(self.load(data, wd="svc", files=files).layers), 2)
        self.assertIsNone(self.load(data, wd=".", files=files))

    # S-5 rows, in order.
    def test_file_matched_by_two_layers(self):
        text = self.problems(overrides(layer("api", ["src/**"], "src/api"), CORE))
        self.assertIn('"src/core/inner.py"', text)
        self.assertIn('"api"', text)
        self.assertIn('"core"', text)

    def test_exit_missing(self):
        text = self.problems(overrides(API, layer("core", ["src/core/**"], "src/core/nope.py")))
        self.assertRegex(text, r'"core" 的 exit "src/core/nope.py"：不存在')

    def test_exit_not_matched_by_its_paths(self):
        text = self.problems(overrides(API, layer("core", ["src/core/sub/**"], "src/core/inner.py")))
        self.assertRegex(text, r'"core" 的 exit "src/core/inner.py"：不被本层 paths 匹配')

    def test_directory_exit_needs_all_its_files_in_the_layer(self):
        text = self.problems(overrides(API, layer("core", ["src/core/*.py"], "src/core")))
        self.assertRegex(text, r'"core" 的 exit "src/core"：不被本层 paths 匹配（"src/core/sub/deep.py"）')

    def test_exit_in_test_paths(self):
        text = self.problems(overrides(API, CORE, **{"test-paths": ["src/core/__init__.py"]}))
        self.assertRegex(text, r'"core" 的 exit "src/core" 在 test-paths 里')

    def test_exit_is_a_list(self):
        text = self.problems(overrides(API, layer("core", ["src/core/**"], ["src/core", "src/core/inner.py"])))
        self.assertRegex(text, r'"core" 的 exit 不是一个字符串')

    def test_duplicate_layer_names(self):
        text = self.problems(overrides(CORE, layer("core", ["src/store/**"], "src/store")))
        self.assertRegex(text, r'层名 "core" 重复')

    def test_unknown_key(self):
        text = self.problems(overrides(API, {**CORE, "exits": "src/core"}))
        self.assertRegex(text, r'层 "core" 有未知键 "exits"')

    def test_parent_or_absolute_paths(self):
        text = self.problems(overrides(API, layer("core", ["src/core/**", "../lib/**"], "src/core")))
        self.assertRegex(text, r'层 "core" 的 paths "../lib/\*\*"')
        text = self.problems(overrides(API, layer("core", ["src/core/**"], "/src/core")))
        self.assertRegex(text, r'层 "core" 的 exit "/src/core"')
        text = self.problems(overrides(API, CORE, **{"test-paths": ["../t/**"]}))
        self.assertIn("test-paths", text)

    def test_exit_not_a_module(self):
        text = self.problems(overrides(API, layer("core", ["core/**"], "core/README.md")))
        self.assertRegex(text, r'"core" 的 exit "core/README.md"：不是 Rust、Python、TypeScript 或 Go 的模块')
        text = self.problems(overrides(API, layer("core", ["core/**"], "core")))
        self.assertRegex(text, r'"core" 的 exit "core"：不是')

    def test_python_paths_outside_source_dirs(self):
        text = self.problems(overrides(API, layer("core", ["src/core/**", "tests/**"], "src/core")))
        self.assertRegex(text, r'层 "core" 的 paths "tests/\*\*" 不在 source-dirs \["src"\] 里')
        self.assertIsNotNone(self.load(overrides(API, CORE, **{"source-dirs": ["."]})))

    def test_shape_problems(self):
        self.assertIn("layers 不是表的数组", self.problems({"project": {".": {"layers": "x"}}}))
        self.assertIn("layers 第 1 项不是表", self.problems(overrides("x")))
        self.assertIn('缺少键 "exit"', self.problems(overrides({"name": "core", "paths": []})))
        self.assertIn("的 paths 不是字符串列表", self.problems(overrides(layer("core", "src/**", "src/core"))))
        self.assertIn("的 name 不是非空字符串", self.problems(overrides(layer("", ["src/**"], "src/core"))))
        self.assertIn("test-paths 不是字符串列表", self.problems(overrides(CORE, **{"test-paths": "tests"})))
        self.assertIn("source-dirs 不是字符串列表", self.problems(overrides(CORE, **{"source-dirs": "src"})))
        self.assertIn("is not a table", self.problems({"project": {".": "x"}}))

    def test_go_directory_exit(self):
        data = overrides(layer("core", ["pkg/go/*"], "pkg/go"))
        self.assertEqual(self.load(data, language="go").layers[0].exit_file, "pkg/go")


LAYERS = """
[project."{wd}"]
{extra}
layers = [
  {{ name = "api", paths = ["{src}/api/**"], exit = "{src}/api" }},
  {{ name = "core", paths = ["{src}/core/**"], exit = "{src}/core" }},
  {{ name = "store", paths = ["{src}/store/**"], exit = "{src}/store" }},
]
"""
BASE = {
    "src/api/__init__.py": "",
    "src/api/a.py": "",
    "src/core/__init__.py": "",
    "src/core/inner.py": "",
    "src/store/__init__.py": "",
    "src/store/inner.py": "",
    "src/util.py": "",
}


def config(wd=".", extra="", src="src"):
    return LAYERS.format(wd=wd, extra=extra, src=src)


class Repo:
    """A git repo: a base commit on main, then a pr branch for head commits."""

    def __init__(self, test, files, toml=None):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.test = test
        self.root = Path(tmp.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.commit(files, toml)
        self.git("checkout", "-q", "-b", "pr")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def commit(self, files, toml=None):
        if toml is not None:
            files = {**files, ".github/checks/repo-guards.toml": toml}
        for path, text in files.items():
            target = self.root / path
            if text is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "c")

    def run(self, language="python", wd=".", base="main"):
        out = io.StringIO()
        summary = self.root.parent / "summary.md"
        summary.write_text("")
        self.code = lr.check(self.root, language, wd, base, out, str(summary))
        self.output = out.getvalue()
        self.summary = summary.read_text()
        return self.code

    def errors(self):
        return [line for line in self.output.splitlines() if line.startswith("::error ")]


class Helpers:
    def repo(self, head=None, base=None, toml=None, head_toml=None):
        repo = Repo(self, {**BASE, **(base or {})}, config() if toml is None else toml)
        if head is not None or head_toml is not None:
            repo.commit(head or {}, head_toml)
        return repo

    def assert_exit(self, repo, code, **kwargs):
        self.assertEqual(repo.run(**kwargs), code, repo.output)


class Check(Helpers, unittest.TestCase):
    def test_s1_no_layers_lists_projects_that_have_them(self):
        repo = self.repo(toml='[project."app"]\nlayers = []\n')
        self.assert_exit(repo, 0)
        self.assertIn('没有 python 的 layers', repo.output)
        self.assertIn('"app"', repo.output)
        repo = Repo(self, BASE)
        self.assert_exit(repo, 0)
        self.assertIn("（无）", repo.output)

    def test_s2_import_of_the_next_layers_exit_passes(self):
        files = {f"svc/{p}": t for p, t in BASE.items()}
        repo = Repo(self, files, config(wd="svc"))
        repo.commit({"svc/src/api/a.py": "from core import thing\nimport core\n"})
        self.assert_exit(repo, 0, wd="svc")

    def test_s3_test_paths_are_not_checked(self):
        extra = 'source-dirs = ["src", "tests"]\ntest-paths = ["tests/**"]'
        toml = config(extra=extra).replace('paths = ["src/api/**"]', 'paths = ["src/api/**", "tests/**"]')
        repo = self.repo(head={"tests/test_api.py": "import store.inner\nimport core.inner\n"}, toml=toml)
        self.assert_exit(repo, 0)

    def test_s4_without_test_paths_tests_are_code(self):
        toml = config(extra='source-dirs = ["src", "tests"]').replace(
            'paths = ["src/api/**"]', 'paths = ["src/api/**", "tests/**"]'
        )
        repo = self.repo(head={"tests/test_api.py": "import store.inner\n"}, toml=toml)
        self.assert_exit(repo, 1)

    def test_s5_invalid_config_exits_2(self):
        repo = self.repo(toml=config().replace('exit = "src/core"', 'exits = "src/core"'))
        self.assert_exit(repo, 2)
        self.assertIn('"core"', repo.output)
        self.assertIn('"exits"', repo.output)

    def test_s6_layer_rules(self):
        rows = [
            ("src/api/a.py", "from core import x\n", 0, None),
            ("src/api/a.py", "import core.inner\n", 1, "绕开出口"),
            ("src/api/a.py", "import store\n", 1, "越层"),
            ("src/store/inner.py", "import core\n", 1, "向上"),
            ("src/core/inner.py", "import core\nfrom core import inner\n", 0, None),
        ]
        for path, text, code, name in rows:
            with self.subTest(text=text):
                repo = self.repo(head={path: text})
                self.assert_exit(repo, code)
                if name:
                    self.assertIn(name, repo.errors()[0])
                else:
                    self.assertEqual(repo.errors(), [])

    def test_s7_new_cycle_in_a_layer(self):
        repo = self.repo(base={"src/core/a.py": "import core.b\n", "src/core/b.py": ""})
        repo.commit({"src/core/b.py": "import core.a\n"})
        self.assert_exit(repo, 1)
        self.assertIn("循环", repo.errors()[0])

    def test_s8_unassigned_targets_are_counted_external_ones_ignored(self):
        repo = self.repo(head={"src/api/a.py": "import util\nimport requests\nimport json\n"})
        self.assert_exit(repo, 0)
        self.assertIn("未归层 import：1 处", repo.summary)
        self.assertNotIn("requests", repo.output)
        self.assertNotIn("json", repo.output)

    def test_s9_type_checking_import_counts(self):
        text = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from core.inner import T\n"
        self.assert_exit(self.repo(head={"src/api/a.py": text}), 1)

    def test_s10_dynamic_import_does_not_count(self):
        text = "import importlib\nimportlib.import_module('store.inner')\n"
        self.assert_exit(self.repo(head={"src/api/a.py": text}), 0)

    def test_s11_source_dirs_resolution(self):
        self.assert_exit(self.repo(head={"src/api/a.py": "import core.inner\n"}), 1)
        toml = config(extra='source-dirs = ["lib"]', src="lib")
        files = {p.replace("src/", "lib/", 1): t for p, t in BASE.items()}
        repo = Repo(self, files, toml)
        repo.commit({"lib/api/a.py": "import core.inner\n"})
        self.assert_exit(repo, 1)

    def test_s12_code_is_never_run(self):
        payload = "open('pwned.txt', 'w').write('x')\n"
        repo = self.repo(head={"src/api/a.py": payload, "src/core/inner.py": payload, "src/sitecustomize.py": payload})
        before = set(Path(tempfile.gettempdir()).glob("**/pwned.txt"))
        self.assert_exit(repo, 0)
        self.assertEqual(list(repo.root.rglob("pwned.txt")), [])
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("**/pwned.txt")), before)

    def test_s13_existing_violation_passes_and_is_summarised(self):
        repo = self.repo(base={"src/api/a.py": "import store.inner\n"}, head={"src/util.py": "x = 1\n"})
        self.assert_exit(repo, 0)
        self.assertIn("| 越层 | `src/api/a.py` | `api` | `src/store/inner.py` | `store` | 1 | 1 |", repo.summary)

    def test_s14_new_config_does_not_block_existing_code(self):
        repo = self.repo(base={"src/api/a.py": "import store.inner\n"}, toml="", head_toml=config())
        self.assert_exit(repo, 0)

    def test_s15_moved_file_counts_as_new(self):
        repo = self.repo(base={"src/api/a.py": "import store\n"})
        repo.git("mv", "src/api/a.py", "src/api/b.py")
        repo.commit({})
        self.assert_exit(repo, 1)
        self.assertTrue(repo.errors()[0].startswith("::error file=src/api/b.py,line=1,"))

    def test_s16_old_cycle_passes_new_edge_closing_one_fails(self):
        base = {"src/core/a.py": "import core.b\n", "src/core/b.py": "import core.a\n"}
        repo = self.repo(base=base, head={"src/util.py": "x = 1\n"})
        self.assert_exit(repo, 0)
        repo.commit({"src/core/c.py": "import core.a\n", "src/core/b.py": "import core.a\nimport core.c\n"})
        self.assert_exit(repo, 1)

    def test_s17_annotation(self):
        files = {f"svc/{p}": t for p, t in BASE.items()}
        repo = Repo(self, files, config(wd="svc"))
        repo.commit({"svc/src/api/a.py": "import os\n\nimport core.inner\n"})
        self.assert_exit(repo, 1, wd="svc")
        [error] = repo.errors()
        self.assertTrue(error.startswith("::error file=svc/src/api/a.py,line=3,title=Layer rules::"), error)
        for text in ("绕开出口", "api", "core", "svc/src/core", "merge-base 0 处", "head 1 处"):
            self.assertIn(text, error)

    def test_s18_one_annotation_per_reference_of_each_new_edge(self):
        base = {"src/core/a.py": "import core.b\n", "src/core/b.py": "import core.c\n", "src/core/c.py": ""}
        repo = self.repo(base=base)
        repo.commit({"src/core/c.py": "x = 1\nimport core.a\n"})
        self.assert_exit(repo, 1)
        [error] = repo.errors()
        self.assertTrue(error.startswith("::error file=src/core/c.py,line=2,"), error)
        self.assertIn("src/core/a.py → src/core/b.py → src/core/c.py → src/core/a.py", error)
        repo.commit({"src/core/c.py": "x = 1\nimport core.a\nfrom core import a\n"})
        self.assert_exit(repo, 1)
        self.assertEqual([e.split(",")[1] for e in repo.errors()], ["line=2", "line=3"])

    def test_s19_unparsable_head_file_exits_2(self):
        repo = self.repo(head={"src/core/bad.py": "def (:\n"})
        self.assert_exit(repo, 2)
        self.assertIn('"src/core/bad.py"', repo.output)

    def test_s20_fixing_a_broken_base_file_passes(self):
        repo = self.repo(base={"src/core/bad.py": "def (:\n"}, head={"src/core/bad.py": "import core\n"})
        self.assert_exit(repo, 0)
        self.assertIn('merge-base 上 "src/core/bad.py" 解析不了', repo.output)

    def test_s24_only_the_working_directory(self):
        files = {**{f"a/{p}": t for p, t in BASE.items()}, **{f"b/{p}": t for p, t in BASE.items()}}
        repo = Repo(self, files, config(wd="a") + config(wd="b"))
        repo.commit({"b/src/api/a.py": "import store\n"})
        self.assert_exit(repo, 0, wd="a")
        self.assert_exit(repo, 1, wd="b")

    def test_s27_counts_per_file_pair_may_only_go_down(self):
        for before, after, code in ((1, 2, 1), (2, 2, 0), (2, 1, 0)):
            with self.subTest(before=before, after=after):
                repo = self.repo(
                    base={"src/api/a.py": "import store.inner\n" * before},
                    head={"src/api/a.py": "import store.inner\n" * after},
                )
                self.assert_exit(repo, code)

    def test_s28_moving_a_reference_within_the_file_passes(self):
        lines = ["x = 1\n"] * 25
        base, head = list(lines), list(lines)
        base[2] = "import store.inner\n"
        head[19] = "import store.inner\n"
        repo = self.repo(base={"src/api/a.py": "".join(base)}, head={"src/api/a.py": "".join(head)})
        self.assert_exit(repo, 0)

    def test_s29_one_reference_counts_once(self):
        rows = [
            ("from store.inner import x\n", "from store.inner import x, y\n", 0),
            ("from store.inner import x\n", "from store.inner import x\nimport store.inner\n", 1),
        ]
        for before, after, code in rows:
            with self.subTest(after=after):
                repo = self.repo(base={"src/api/a.py": before}, head={"src/api/a.py": after})
                self.assert_exit(repo, code)

    def test_s30_more_references_on_an_old_cycle_edge_pass(self):
        base = {"src/core/a.py": "import core.b\n", "src/core/b.py": "import core.a\n"}
        repo = self.repo(base=base, head={"src/core/b.py": "import core.a\nfrom core import a\n"})
        self.assert_exit(repo, 0)

    def test_s31_every_reference_of_a_grown_pair_is_annotated(self):
        lines = ["x = 1\n"] * 25
        lines[2] = "import store.inner\n"
        base = "".join(lines)
        lines[19] = "import store.inner\n"
        repo = self.repo(base={"src/api/a.py": base}, head={"src/api/a.py": "".join(lines)})
        self.assert_exit(repo, 1)
        errors = repo.errors()
        self.assertEqual([e.split(",")[1] for e in errors], ["line=3", "line=20"])
        for text in ("越层", "merge-base 1 处", "head 2 处", "不超过 1 处"):
            self.assertIn(text, errors[0])

    def test_s32_shrinking_pairs_are_summarised(self):
        for before, after in ((2, 1), (1, 0)):
            with self.subTest(before=before, after=after):
                repo = self.repo(
                    base={"src/api/a.py": "import store.inner\n" * before},
                    head={"src/api/a.py": "import store.inner\n" * after},
                )
                self.assert_exit(repo, 0)
                self.assertIn(f"| `store` | {before} | {after} |", repo.summary)

    def test_symlinks_and_uncommitted_changes_are_not_read(self):
        repo = self.repo()
        os.symlink("../store/inner.py", repo.root / "src/api/link.py")
        repo.commit({})
        (repo.root / "src/api/a.py").write_text("import store\n")
        self.assert_exit(repo, 0)
        self.assertIn("未提交的改动", repo.output)

    def test_default_base_needs_origin_head(self):
        repo = self.repo()
        self.assert_exit(repo, 2, base=None)
        self.assertIn("--base", repo.output)


TS_LAYERS = """
[project."{wd}"]
{extra}
layers = [
  {{ name = "api", paths = ["src/api/**"], exit = "src/api/index.ts" }},
  {{ name = "core", paths = ["src/core/**"], exit = "src/core/index.ts" }},
  {{ name = "store", paths = ["src/store/**"], exit = "src/store/index.ts" }},
]
"""
TS_BASE = {
    "src/api/index.ts": "",
    "src/api/a.ts": "",
    "src/core/index.ts": "",
    "src/core/inner.ts": "",
    "src/store/index.ts": "",
    "src/store/inner.ts": "",
    "src/util.ts": "",
}


class TypeScript(Helpers, unittest.TestCase):
    def ts_repo(self, head=None, base=None, extra="", wd="."):
        prefix = "" if wd == "." else f"{wd}/"
        files = {f"{prefix}{p}": t for p, t in {**TS_BASE, **(base or {})}.items()}
        repo = Repo(self, files, TS_LAYERS.format(wd=wd, extra=extra))
        if head is not None:
            repo.commit({f"{prefix}{p}": t for p, t in head.items()})
        return repo

    def assert_ts(self, repo, code, wd="."):
        self.assert_exit(repo, code, language="typescript", wd=wd)

    def test_s2_import_of_the_next_layers_exit_passes(self):
        text = 'import { x } from "../core/index";\nimport "../core";\nimport "../core/index.js";\n'
        repo = self.ts_repo(head={"src/api/a.ts": text}, wd="svc")
        self.assert_ts(repo, 0, wd="svc")

    def test_s3_test_paths_are_not_checked(self):
        repo = self.ts_repo(head={"src/api/a.test.ts": 'import "../core/inner";\n'}, extra='test-paths = ["**/*.test.ts"]')
        self.assert_ts(repo, 0)
        repo = self.ts_repo(head={"src/api/a.test.ts": 'import "../core/inner";\n'})
        self.assert_ts(repo, 1)

    def test_s7_new_cycle_in_a_layer(self):
        repo = self.ts_repo(base={"src/core/a.ts": 'import "./b";\n', "src/core/b.ts": ""})
        repo.commit({"src/core/b.ts": 'import "./a";\n'})
        self.assert_ts(repo, 1)
        self.assertIn("循环", repo.errors()[0])

    def test_s9_static_references_count(self):
        for text in [
            'import type { T } from "../core/inner";\n',
            'export { x } from "../core/inner";\n',
            'const m = require("../core/inner");\n',
        ]:
            with self.subTest(text=text):
                repo = self.ts_repo(head={"src/api/a.ts": text})
                self.assert_ts(repo, 1)
                self.assertIn("绕开出口", repo.errors()[0])

    def test_dynamic_import_does_not_count(self):
        repo = self.ts_repo(head={"src/api/a.ts": 'const m = await import("../store/inner");\n'})
        self.assert_ts(repo, 0)
        self.assertEqual(repo.errors(), [])

    def test_s11_tsconfig_paths(self):
        for options in ['{"paths": {"@core/*": ["src/core/*"]}}', '{"baseUrl": "src", "paths": {"@core/*": ["core/*"]}}']:
            with self.subTest(options=options):
                tsconfig = '{"compilerOptions": ' + options + "}"
                repo = self.ts_repo(base={"tsconfig.json": tsconfig}, head={"src/api/a.ts": 'import { x } from "@core/inner";\n'})
                self.assert_ts(repo, 1)
                self.assertIn("绕开出口", repo.errors()[0])

    def test_s29_one_reference_counts_once(self):
        repo = self.ts_repo(
            base={"src/api/a.ts": 'import { a } from "../store/inner";\n'},
            head={"src/api/a.ts": 'import { a, b } from "../store/inner";\n'},
        )
        self.assert_ts(repo, 0)

    def test_s23_python_and_typescript_in_one_directory(self):
        toml = """
[project."."]
layers = [
  { name = "api", paths = ["src/api/**"], exit = "src/api/__init__.py" },
  { name = "core", paths = ["src/core/**"], exit = "src/core/__init__.py" },
  { name = "ui", paths = ["web/ui/**"], exit = "web/ui/index.ts" },
  { name = "client", paths = ["web/client/**"], exit = "web/client/index.ts" },
]
"""
        files = {
            "src/api/__init__.py": "",
            "src/api/a.py": "import core\n",
            "src/core/__init__.py": "",
            "src/core/inner.py": "",
            "web/ui/index.ts": "",
            "web/ui/a.ts": "",
            "web/client/index.ts": "",
            "web/client/inner.ts": "",
        }
        repo = Repo(self, files, toml)
        repo.commit({"web/ui/a.ts": 'import "../client/inner";\n'})
        self.assert_exit(repo, 0, language="python")
        self.assert_ts(repo, 1)

    def test_s19_s20_broken_files(self):
        repo = self.ts_repo(head={"src/core/bad.ts": "const = ;\n"})
        self.assert_ts(repo, 2)
        self.assertIn('"src/core/bad.ts"', repo.output)
        repo = self.ts_repo(base={"src/core/bad.ts": "const = ;\n"}, head={"src/core/bad.ts": 'import "./index";\n'})
        self.assert_ts(repo, 0)
        self.assertIn('merge-base 上 "src/core/bad.ts" 解析不了', repo.output)

    def test_s12_code_and_plugins_are_never_run(self):
        payload = 'import { writeFileSync } from "node:fs";\nwriteFileSync("pwned.txt", "x");\n'
        tsconfig = '{"extends": "@tsconfig/node22/tsconfig.json", "compilerOptions": {"plugins": [{"name": "evil"}]}}'
        repo = self.ts_repo(head={"src/api/a.ts": payload, "src/core/inner.ts": payload, "tsconfig.json": tsconfig})
        before = set(Path(tempfile.gettempdir()).glob("**/pwned.txt"))
        self.assert_ts(repo, 0)
        self.assertEqual(list(repo.root.rglob("pwned.txt")), [])
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("**/pwned.txt")), before)

    def test_command_line(self):
        repo = self.ts_repo(head={"src/api/a.ts": 'import "../store/inner";\n'})
        repo.run(language="typescript")
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(Path(lr.__file__)), "typescript", "--base", "main"],
            cwd=repo.root,
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"},
        )
        self.assertEqual(repo.code, 1, repo.output)
        self.assertEqual((result.returncode, result.stdout), (repo.code, repo.output))
        self.assertIn("::error file=src/api/a.ts,line=1,", result.stdout)


class MoreChecks(Helpers, unittest.TestCase):
    def test_other_languages_layers_are_not_checked(self):
        files = {**BASE, "web/index.ts": "", "web/inner.ts": ""}
        bad_ui = '{ name = "ui", paths = ["../web/**"], exits = 1, exit = "web/index.ts" },'
        repo = Repo(self, files, config().replace("layers = [\n", "layers = [\n  " + bad_ui + "\n"))
        repo.commit({"src/api/a.py": "import store\n"})
        self.assert_exit(repo, 1)
        repo = Repo(self, files, '[project."."]\nlayers = [\n  ' + bad_ui + "\n]\n")
        self.assert_exit(repo, 0)
        self.assertIn("没有 python 的 layers", repo.output)

    def test_hash_in_worker_paths_is_escaped_everywhere(self):
        name = "src/api/##[error]forged.py"
        repo = self.repo(head={name: "import store.inner\n", "src/core/##[warning]bad.py": "def (:\n"})
        self.assert_exit(repo, 2)
        self.assertNotIn("##[", repo.output)
        repo = self.repo(base={name: "def (:\n"}, head={name: "import store.inner\n"})
        self.assert_exit(repo, 1)
        for line in repo.output.splitlines():
            if not line.startswith("::error "):
                self.assertNotIn("##[", line)
        self.assertNotIn("##[", repo.summary)

    def test_only_scanned_and_resolution_files_are_written(self):
        seen = {}

        def runner(argv, **kwargs):
            side = Path(argv[-1])
            if "request" not in seen:
                seen["written"] = sorted(str(p.relative_to(side)) for p in side.rglob("*") if p.is_file())
                seen["request"] = json.loads(kwargs["input"])
                seen["cwd"] = kwargs["cwd"]
            return subprocess.CompletedProcess(argv, 0, "", "")

        repo = self.repo(head={"src/other/x.py": "", "pyproject.toml": ""})
        out = io.StringIO()
        self.assertEqual(lr.check(repo.root, "python", ".", "main", out, None, runner), 0, out.getvalue())
        self.assertEqual(seen["cwd"], "/")
        self.assertNotIn("src/other/x.py", seen["written"])
        self.assertIn("src/other/x.py", seen["request"]["files"])
        self.assertEqual(seen["request"]["context"], [])
        self.assertEqual(seen["request"]["settings"], {"source-dirs": ["src"]})

    def test_local_command_matches_the_library_call(self):
        repo = self.repo(base={"src/api/a.py": "import store.inner\n"}, head={"src/api/a.py": "import store.inner\n" * 2})
        repo.run()
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(Path(lr.__file__)), "python", "--working-directory", ".", "--base", "main"],
            cwd=repo.root,
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"},
        )
        self.assertEqual((result.returncode, result.stdout), (repo.code, repo.output))


class PackageUnits(unittest.TestCase):
    """Go's unit is the package directory: pairs and edges name it, and its exit is the directory."""

    def config(self):
        layers = (
            lr.Layer("api", ("api/**",), "api", "api", "go"),
            lr.Layer("core", ("core/**",), "core", "core", "go"),
        )
        return lr.Config(layers, (), ())

    def test_targets_map_to_their_package(self):
        config, spec, side = self.config(), lr.LANGUAGES["go"], lr.Side()
        lr.add(side, config, spec, "api/a.go", 3, ["core/x.go", "core/inner/y.go", "api/b.go"])
        lr.add(side, config, spec, "core/inner/y.go", 4, ["core/x.go"])
        self.assertEqual(dict(side.pairs), {("api/a.go", "core/inner"): [3]})
        self.assertEqual(dict(side.edges), {("core/inner", "core"): [("core/inner/y.go", 4)]})
        report = lr.judge(config, "", side, lr.Side())
        self.assertIn("绕开出口", report.lines[0])
        self.assertIn("api/a.go 引用 core/inner", report.lines[0])


class GoProtocol(unittest.TestCase):
    LAYERS = (
        '[project."."]\ntest-paths = ["**/*_test.go"]\nlayers = [\n'
        '  { name = "api", paths = ["api/**"], exit = "api" },\n'
        '  { name = "core", paths = ["core/**"], exit = "core" },\n'
        '  { name = "store", paths = ["store/**"], exit = "store" },\n]\n'
    )
    FILES = {"go.mod": "module m\n", "api/a.go": "", "core/c.go": "", "store/a_test.go": "", "store/store.go": ""}

    def test_package_target_is_judged_by_its_non_test_files(self):
        def runner(argv, **kwargs):
            side = Path(argv[-1])
            request = json.loads(kwargs["input"])
            self.assertEqual(request["context"], ["go.mod"])
            self.assertTrue((side / "go.mod").is_file())
            imports = (side / "api/a.go").read_text() if (side / "api/a.go").exists() else ""
            out = json.dumps({"file": "api/a.go", "line": 1, "targets": ["store"]}) if "store" in imports else ""
            return subprocess.CompletedProcess(argv, 0, out, "")

        repo = Repo(self, self.FILES, self.LAYERS)
        repo.commit({"api/a.go": 'import "m/store"\n'})
        go = dataclasses.replace(lr.LANGUAGES["go"], scanner=lambda root: ["scanner", str(root)])
        out = io.StringIO()
        with unittest.mock.patch.dict(lr.LANGUAGES, {"go": go}):
            code = lr.check(repo.root, "go", ".", "main", out, None, runner)
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("越层：api → store，api/a.go 引用 store。", out.getvalue())

    def test_package_split_across_layers_is_invalid(self):
        files = frozenset({"api/a.go", "core/c.go", "lib/x.go", "lib/y.go", "lib/z_test.go"})
        data = {
            "project": {
                ".": {
                    "test-paths": ["**/*_test.go"],
                    "layers": [
                        {"name": "api", "paths": ["api/**", "lib/x.go"], "exit": "api"},
                        {"name": "core", "paths": ["core/**", "lib/y.go"], "exit": "core"},
                    ],
                }
            }
        }
        with self.assertRaises(lr.ConfigError) as caught:
            lr.load_config(data, ".", "go", files)
        self.assertIn('包 "lib" 的文件分在层 "api"、"core" 里', "\n".join(caught.exception.problems))


class Workflow(unittest.TestCase):
    WORKFLOWS = lr.CI_CHECKS.parent / ".github/workflows"

    def steps(self, language):
        text = (self.WORKFLOWS / f"{language}.yml").read_text()
        job = text.split("\n  checks:\n", 1)[1].split("\n  coverage:\n", 1)[0]
        steps = re.split(r"\n      - (?=name: |&|\*)", job)[1:]
        names = [m.group(1) if (m := re.search(r"^(?:&\S+\s+)?name: (.*)$", step, re.M)) else step for step in steps]
        return names, steps

    def test_s21_s22_layer_rules_follows_inline_exemptions_and_blocks_nothing(self):
        for language in ("python", "typescript"):
            with self.subTest(language=language):
                names, steps = self.steps(language)
                at = names.index("Inline exemptions")
                self.assertEqual(names[at + 1], "Layer rules")
                step = steps[at + 1]
                self.assertIn("if: ${{ !cancelled() && steps.inline-exemptions.outcome == 'success' }}", step)
                self.assertNotIn("continue-on-error", step)
                self.assertIn("working-directory: repo\n", step)
                self.assertIn("UV_PYTHON_INSTALL_DIR: ${{ runner.temp }}/layer-rules-python", step)
                if language == "typescript":
                    self.assertIn("PROJECT_DIR: ${{ inputs.working-directory }}", step)
                self.assertIn(
                    "python=$(cd / && uv python install --no-config -q 3.14 && uv python find --no-config --managed-python 3.14)",
                    step,
                )
                command = f'"$python" -I -S "$RG/layer-rules/layer_rules.py" {language} --working-directory "$PROJECT_DIR" --base "$BASE_REF"'
                self.assertIn(command, step)
                for later in steps[at + 2 :]:
                    self.assertIn("!cancelled() && steps.inline-exemptions.outcome == 'success'", later)

    def test_typescript_scanner_is_installed_and_only_repo_guards_steps_set_the_environment(self):
        names, steps = self.steps("typescript")
        tools = steps[names.index("Install the shared tools")]
        self.assertIn('npm ci --prefix "$RG/typescript/layer-rules" --omit=dev --no-audit --no-fund', tools)
        writers = [name for name, step in zip(names[: names.index("Layer rules")], steps) if re.search(r"GITHUB_(ENV|PATH)", step)]
        self.assertEqual(writers, ["Apply the shared .npmrc", "Install the shared tools"])
        npmrc = (lr.CI_CHECKS / "typescript/.npmrc").read_text().splitlines()
        self.assertIn("ignore-scripts=true", npmrc)


class Docs(unittest.TestCase):
    ROOT = lr.CI_CHECKS.parent

    def test_s26_docs_name_the_keys_and_the_step(self):
        ci = (self.ROOT / "ci-checks/README.md").read_text()
        section = ci.split("### 覆盖默认配置", 1)[1].split("\n## ", 1)[0]
        self.assertIn("  - `layers`：", section)
        self.assertIn("  - `test-paths`：", section)
        self.assertIn('layers = [\n  { name = "api"', section)
        self.assertIn("| `python.yml` | Changes（Match changed files，只在 PR 上跑）、Checks（Inline exemptions、Layer rules、Format、", ci)
        arch = (self.ROOT / "architecture/README.md").read_text()
        self.assertIn("| 2 | [分层规则](#第-2-项分层规则) |", arch)
        self.assertIn("\n### 第 2 项分层规则\n", arch)


class Output(unittest.TestCase):
    def test_workflow_commands_cannot_be_injected(self):
        line = lr.annotation("a,b:c%.py", 3, "x\n::warning::y %0A")
        self.assertEqual(line, "::error file=a%2Cb%3Ac%25.py,line=3,title=Layer rules::x%0A::warning::y %250A")
        self.assertEqual(len(line.splitlines()), 1)

    def test_code_spans_hold_any_path(self):
        self.assertEqual(lr.code("a|b"), "`a\\|b`")
        self.assertEqual(lr.code("a``b"), "```a``b```")
        self.assertEqual(lr.code("`x"), "`` `x ``")
        self.assertEqual(lr.code("a\n##[error]b"), "`a\\n\\u0023\\u0023[error]b`")
        self.assertEqual(lr.quote("x ##[add-mask]y"), '"x \\u0023\\u0023[add-mask]y"')

    def test_command_line(self):
        repo = Repo(self, BASE, config())
        repo.commit({"src/api/a.py": "import store\n"})
        script = Path(lr.__file__)
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(script), "python", "--base", "main"],
            cwd=repo.root,
            capture_output=True,
            text=True,
            env={**os.environ, "GITHUB_STEP_SUMMARY": ""},
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("::error file=src/api/a.py,line=1,", result.stdout)


if __name__ == "__main__":
    unittest.main()
