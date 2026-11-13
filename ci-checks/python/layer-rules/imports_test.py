import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCANNER = HERE / "imports.py"


class Scan(unittest.TestCase):
    def run_scanner(self, files, scan=None, source_dirs=("src",)):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "side"
        for path, text in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(text if isinstance(text, bytes) else text.encode())
        request = {
            "files": sorted(files),
            "scan": sorted(files if scan is None else scan),
            "source-dirs": list(source_dirs),
        }
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(SCANNER), str(root)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            cwd=tmp.name,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.root = root
        return [json.loads(line) for line in result.stdout.splitlines()]

    def sites(self, files, **kwargs):
        return [(r["file"], r["line"], r["targets"]) for r in self.run_scanner(files, **kwargs) if "targets" in r]

    def test_absolute_import_resolves_through_source_dirs(self):
        files = {
            "src/api/a.py": "import core.inner\nfrom core import inner\nfrom core.inner import x\n",
            "src/core/__init__.py": "",
            "src/core/inner.py": "x = 1\n",
        }
        self.assertEqual(
            self.sites(files, scan=["src/api/a.py"]),
            [
                ("src/api/a.py", 1, ["src/core/inner.py"]),
                ("src/api/a.py", 2, ["src/core/inner.py"]),
                ("src/api/a.py", 3, ["src/core/inner.py"]),
            ],
        )

    def test_package_import_targets_its_init(self):
        files = {"src/api/a.py": "from core import Thing\nimport core\n", "src/core/__init__.py": "Thing = 1\n"}
        self.assertEqual(
            self.sites(files, scan=["src/api/a.py"]),
            [("src/api/a.py", 1, ["src/core/__init__.py"]), ("src/api/a.py", 2, ["src/core/__init__.py"])],
        )

    def test_source_dir_dot_and_several_source_dirs(self):
        files = {"app/a.py": "import lib.b\nimport tools.c\n", "lib/b.py": "", "tools/c.py": ""}
        self.assertEqual(
            self.sites(files, scan=["app/a.py"], source_dirs=(".",)),
            [("app/a.py", 1, ["lib/b.py"]), ("app/a.py", 2, ["tools/c.py"])],
        )
        files = {"src/a.py": "import c\n", "tools/c.py": ""}
        self.assertEqual(
            self.sites(files, scan=["src/a.py"], source_dirs=("src", "tools")), [("src/a.py", 1, ["tools/c.py"])]
        )

    def test_relative_imports(self):
        files = {
            "src/pkg/__init__.py": "from . import m\n",
            "src/pkg/m.py": "from .sub.n import y\nfrom .. import top\nfrom . import *\n",
            "src/pkg/sub/n.py": "y = 1\n",
            "src/top.py": "",
        }
        self.assertEqual(
            self.sites(files, scan=["src/pkg/__init__.py", "src/pkg/m.py"]),
            [
                ("src/pkg/__init__.py", 1, ["src/pkg/m.py"]),
                ("src/pkg/m.py", 1, ["src/pkg/sub/n.py"]),
                ("src/pkg/m.py", 2, ["src/top.py"]),
                ("src/pkg/m.py", 3, ["src/pkg/__init__.py"]),
            ],
        )

    def test_one_statement_is_one_site_with_each_target_once(self):
        files = {
            "src/a.py": "import b, c\nfrom pkg import m, n, value\nfrom b import x, y\n",
            "src/b.py": "",
            "src/c.py": "",
            "src/pkg/__init__.py": "value = 1\n",
            "src/pkg/m.py": "",
            "src/pkg/n.py": "",
        }
        self.assertEqual(
            self.sites(files, scan=["src/a.py"]),
            [
                ("src/a.py", 1, ["src/b.py", "src/c.py"]),
                ("src/a.py", 2, ["src/pkg/__init__.py", "src/pkg/m.py", "src/pkg/n.py"]),
                ("src/a.py", 3, ["src/b.py"]),
            ],
        )

    def test_every_static_import_counts_dynamic_ones_do_not(self):
        files = {
            "src/a.py": (
                "from typing import TYPE_CHECKING\n"
                "import importlib\n"
                "if TYPE_CHECKING:\n"
                "    from store import inner\n"
                "def f():\n"
                "    import store.inner\n"
                "importlib.import_module('store.inner')\n"
                "__import__('store.inner')\n"
            ),
            "src/store/__init__.py": "",
            "src/store/inner.py": "",
        }
        self.assertEqual(
            self.sites(files, scan=["src/a.py"]),
            [("src/a.py", 4, ["src/store/inner.py"]), ("src/a.py", 6, ["src/store/inner.py"])],
        )

    def test_external_and_self_imports_are_left_out(self):
        files = {"src/a.py": "import json\nimport requests\nimport a\nfrom . import a as same\n"}
        self.assertEqual(self.sites(files), [])

    def test_missing_submodule_falls_back_to_longest_module(self):
        files = {"src/a.py": "import pkg.missing\n", "src/pkg/__init__.py": ""}
        self.assertEqual(self.sites(files, scan=["src/a.py"]), [("src/a.py", 1, ["src/pkg/__init__.py"])])

    def test_stub_only_module(self):
        files = {"src/a.py": "import b\n", "src/b.pyi": ""}
        self.assertEqual(self.sites(files, scan=["src/a.py"]), [("src/a.py", 1, ["src/b.pyi"])])

    def test_only_scan_files_are_parsed(self):
        files = {"src/a.py": "import b\n", "src/b.py": "this is not python\n"}
        self.assertEqual(self.run_scanner(files, scan=["src/a.py"]), [{"file": "src/a.py", "line": 1, "targets": ["src/b.py"]}])

    def test_unparsable_files_are_error_records(self):
        files = {"src/bad.py": "def (:\n", "src/nul.py": b"x = 1\0\n", "src/latin.py": b"# coding: ascii\nx = '\xff'\n"}
        records = self.run_scanner(files)
        self.assertEqual([r["file"] for r in records], ["src/bad.py", "src/latin.py", "src/nul.py"])
        self.assertTrue(all(set(r) == {"file", "error"} and r["error"] for r in records))

    def test_encoding_cookie_is_honoured(self):
        files = {"src/a.py": "# -*- coding: latin-1 -*-\nimport b  # caf\xe9\n".encode("latin-1"), "src/b.py": ""}
        self.assertEqual(self.sites(files, scan=["src/a.py"]), [("src/a.py", 2, ["src/b.py"])])

    def test_code_is_never_executed(self):
        payload = "open('pwned.txt', 'w').write('x')\nimport os\nos.system('touch pwned.txt')\n"
        files = {"src/a.py": payload, "src/sitecustomize.py": payload, "src/usercustomize.py": payload}
        self.sites(files)
        tmp = self.root.parent
        self.assertEqual([p for p in tmp.rglob("pwned.txt")], [])
        self.assertFalse((Path.cwd() / "pwned.txt").exists())
        self.assertFalse(os.path.exists(os.path.join(tempfile.gettempdir(), "pwned.txt")))


class Usage(unittest.TestCase):
    def test_bad_request_exits_2(self):
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(SCANNER), "/nonexistent"], input="not json", capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2)
        result = subprocess.run([sys.executable, "-I", "-S", str(SCANNER)], input="{}", capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
