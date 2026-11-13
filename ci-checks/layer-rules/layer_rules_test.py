import unittest

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


if __name__ == "__main__":
    unittest.main()
