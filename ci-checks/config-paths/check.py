#!/usr/bin/env python3
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def is_config(path, names):
    base = path.rsplit("/", 1)[-1]
    for n in names:
        if "/" in n:
            if fnmatch.fnmatchcase(path, n) or fnmatch.fnmatchcase(path, "*/" + n):
                return True
        elif fnmatch.fnmatchcase(base, n):
            return True
    return False


def main():
    config = json.loads((HERE / "config.json").read_text())
    known = json.loads((HERE / "config-files.json").read_text())
    names = [n for lang in config["languages"] for n in known[lang]]
    files = subprocess.run(["git", "ls-files"], check=True, capture_output=True, text=True).stdout.splitlines()
    bad = [f for f in files if is_config(f, names) and not any(fnmatch.fnmatchcase(f, a) for a in config["allowed"])]
    if bad:
        print("Tool config or test hook files outside the allowed paths in config.json:", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        return 1
    print(f"{len(files)} files, {len(names)} config file names, none outside the allowed paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
