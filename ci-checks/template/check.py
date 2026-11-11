#!/usr/bin/env python3
import argparse
import fnmatch
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(config_path):
    config = json.loads(Path(config_path).read_text())
    languages = [json.loads((HERE / "languages" / f"{name}.json").read_text()) for name in config["languages"]]
    return config, languages


def tracked_files():
    return subprocess.run(["git", "ls-files"], check=True, capture_output=True, text=True).stdout.splitlines()


def matches(path, patterns):
    name = path.rsplit("/", 1)[-1]
    for p in patterns:
        if "/" in p:
            if fnmatch.fnmatchcase(path, p) or fnmatch.fnmatchcase(path, "*/" + p):
                return True
        elif fnmatch.fnmatchcase(name, p):
            return True
    return False


def indent(line):
    return len(line) - len(line.lstrip())


class Scopes:
    """Function and type scopes by indentation; relies on formatter-enforced layout."""

    def __init__(self, lines, lang):
        self.lines = lines
        self.defs = [re.compile(p) for p in lang["scopes"]]
        self.skip = re.compile(lang["skip_to_next"])

    def name_at(self, i):
        for d in self.defs:
            m = d.search(self.lines[i])
            if m:
                return m["name"]
        return None

    def enclosing(self, i):
        names, level = [], indent(self.lines[i])
        for j in range(i - 1, -1, -1):
            line = self.lines[j]
            if line.strip() and indent(line) < level:
                name = self.name_at(j)
                if name:
                    names.append(name)
                level = indent(line)
                if level == 0:
                    break
        return names[::-1]

    def of(self, i, applies_to_next):
        if applies_to_next:
            j = i + 1
            while j < len(self.lines) and (not self.lines[j].strip() or self.skip.match(self.lines[j])):
                j += 1
            i = min(j, len(self.lines) - 1)
        names = self.enclosing(i)
        own = self.name_at(i)
        if own:
            names.append(own)
        return ".".join(names) or "<module>"


def occurrences(languages):
    found = Counter()
    for lang in languages:
        rules = [(r, re.compile(r["pattern"])) for r in lang["exemptions"]]
        for path in tracked_files():
            if not matches(path, lang["files"]):
                continue
            try:
                lines = Path(path).read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            scopes = Scopes(lines, lang)
            for i, line in enumerate(lines):
                for rule, rx in rules:
                    for m in rx.finditer(line):
                        codes = re.split(r"[,\s]+", (m.groupdict().get("codes") or "").strip())
                        scope = scopes.of(i, rule.get("next", False))
                        for code in [c for c in codes if c] or ["*"]:
                            found[(rule["tool"], path, code, scope)] += 1
    return found


def exemptions(args):
    config, languages = load(args.config)
    found = occurrences(languages)
    if args.print_inventory:
        print(json.dumps([
            {"tool": t, "path": p, "code": c, "scope": s, "count": n, "reason": ""}
            for (t, p, c, s), n in sorted(found.items())
        ], indent=2, ensure_ascii=False))
        return 0
    inventory, errors = {}, []
    for e in config.get("exemptions", []):
        key = (e["tool"], e["path"], e["code"], e["scope"])
        if key in inventory:
            errors.append(f"duplicate entry: {key}")
        inventory[key] = e.get("count", 1)
        if not e.get("reason", "").strip():
            errors.append(f"entry has no reason: {key}")
    for key, n in sorted(found.items()):
        want = inventory.get(key)
        if want is None:
            errors.append(f"not in exemptions: {key} x{n}")
        elif n != want:
            errors.append(f"count {n}, exemptions entry says {want}: {key}")
    for key in sorted(inventory.keys() - found.keys()):
        errors.append(f"entry has no matching code, remove it: {key}")
    print(f"{sum(found.values())} inline exemptions, {len(inventory)} entries", flush=True)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


def config_paths(args):
    config, languages = load(args.config)
    patterns = [p for lang in languages for p in lang["config_files"]]
    allowed = config.get("allowed_config_paths", [])
    bad = [p for p in tracked_files() if matches(p, patterns) and not any(fnmatch.fnmatchcase(p, a) for a in allowed)]
    if bad:
        print("Tool config or test hook files outside allowed_config_paths:", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        return 1
    print(f"{len(patterns)} config file patterns, no file outside allowed_config_paths")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(HERE / "config.json"))
    sub = parser.add_subparsers(dest="check", required=True)
    e = sub.add_parser("exemptions")
    e.add_argument("--print-inventory", action="store_true")
    sub.add_parser("config-paths")
    args = parser.parse_args()
    return exemptions(args) if args.check == "exemptions" else config_paths(args)


if __name__ == "__main__":
    sys.exit(main())
