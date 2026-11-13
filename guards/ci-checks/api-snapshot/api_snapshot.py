import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

Key = tuple[str, str, str, str]


class Entry(NamedTuple):
    # Field order is the sort order: the first four fields are the key.
    unit: str
    parent: str
    kind: str
    name: str
    signature: str

    @property
    def key(self) -> Key:
        return self[:4]


FIELDS = frozenset(Entry._fields)
KEY_FIELDS = Entry._fields[:4]


class Error(Exception):
    pass


@dataclass(frozen=True)
class Diff:
    added: list[Entry]
    removed: list[Entry]
    changed: list[tuple[Entry, Entry]]

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def quote(text: str) -> str:
    # ASCII JSON has no raw line breaks, so no line can start with "::"; "#" is escaped too because
    # the runner also reads the legacy "##[command]" syntax in the middle of a line.
    return json.dumps(text).replace("#", "\\u0023")


def parse(text: str, source: str) -> dict[Key, Entry]:
    try:
        items = json.loads(text)
    except json.JSONDecodeError as error:
        raise Error(f"{source}: not valid JSON: {error.msg} at line {error.lineno} column {error.colno}") from None
    if not isinstance(items, list):
        raise Error(f"{source}: expected a JSON array of entries, got {type(items).__name__}")
    entries: dict[Key, Entry] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise Error(f"{source}: item {index} is not an object")
        if item.keys() != FIELDS:
            missing = sorted(FIELDS - item.keys())
            extra = sorted(item.keys() - FIELDS)
            raise Error(
                f"{source}: item {index} must have exactly the fields {', '.join(Entry._fields)}"
                f"; missing [{', '.join(map(quote, missing))}], extra [{', '.join(map(quote, extra))}]"
            )
        wrong = [field for field in Entry._fields if not isinstance(item[field], str)]
        if wrong:
            raise Error(f"{source}: item {index} has non-string {', '.join(wrong)}")
        entry = Entry(**item)
        if entry.key in entries:
            raise Error(f"{source}: duplicate key {key_json(entry)} at item {index}")
        entries[entry.key] = entry
    return entries


def read(path: Path, source: str) -> dict[Key, Entry]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise Error(f"{source}: cannot read {quote(str(path))}: {type(error).__name__}") from error
    return parse(text, source)


def read_snapshot(path: Path) -> dict[Key, Entry] | None:
    try:
        return read(path, "snapshot")
    except Error as error:
        if isinstance(error.__cause__, FileNotFoundError):
            return None
        raise


def compare(snapshot: dict[Key, Entry], entries: dict[Key, Entry]) -> Diff:
    return Diff(
        added=sorted(entries[key] for key in entries.keys() - snapshot.keys()),
        removed=sorted(snapshot[key] for key in snapshot.keys() - entries.keys()),
        changed=sorted(
            (snapshot[key], entries[key])
            for key in snapshot.keys() & entries.keys()
            if snapshot[key].signature != entries[key].signature
        ),
    )


def render(entries: dict[Key, Entry]) -> str:
    return json.dumps([entry._asdict() for entry in sorted(entries.values())], indent=2, ensure_ascii=False) + "\n"


def key_json(entry: Entry) -> str:
    return "{" + ", ".join(f'"{field}": {quote(getattr(entry, field))}' for field in KEY_FIELDS) + "}"


def log_lines(diff: Diff | None, entries: dict[Key, Entry], artifact: str, path: str) -> list[str]:
    if diff is None:
        lines = [f"api snapshot: {quote(path)} does not exist; {len(entries)} entries generated"]
    elif diff.empty:
        return [f"api snapshot: matches {quote(path)}"]
    else:
        lines = [
            f"api snapshot: {len(diff.added)} added, {len(diff.removed)} removed, {len(diff.changed)} changed"
            f" against {quote(path)}"
        ]
        lines += [f"多 {key_json(entry)} {quote(entry.signature)}" for entry in diff.added]
        lines += [f"少 {key_json(entry)} {quote(entry.signature)}" for entry in diff.removed]
        for old, new in diff.changed:
            lines += [f"变 {key_json(old)}", f"  旧 {quote(old.signature)}", f"  新 {quote(new.signature)}"]
    return lines + [f"new snapshot: artifact {quote(artifact)}, commit it as {quote(path)}"]


def fence(text: str) -> str:
    # A fence longer than any backtick run inside cannot be closed early; code blocks render no Markdown.
    ticks = "`" * max(3, max(map(len, re.findall("`+", text)), default=0) + 1)
    return f"{ticks}text\n{text}\n{ticks}\n"


def entry_text(entry: Entry, signature: list[str]) -> str:
    return "\n".join([*(f"{field}: {getattr(entry, field)}" for field in KEY_FIELDS), *signature])


def summary_text(diff: Diff | None, entries: dict[Key, Entry], artifact: str, path: str) -> str:
    if diff is not None and diff.empty:
        return f"### 接口快照：一致\n\n生成的条目和这个快照一致：\n\n{fence(path)}"
    if diff is None:
        parts = [f"### 接口快照：快照文件不存在\n\n配置的路径上还没有快照文件，这次生成了 {len(entries)} 个条目。\n"]
    else:
        parts = [
            f"### 接口快照：不一致\n\n多 {len(diff.added)} 条、少 {len(diff.removed)} 条、变 {len(diff.changed)} 条。\n"
        ]
        sections = [
            ("多", [entry_text(e, [f"signature: {e.signature}"]) for e in diff.added]),
            ("少", [entry_text(e, [f"signature: {e.signature}"]) for e in diff.removed]),
            (
                "变",
                [
                    entry_text(old, [f"旧签名: {old.signature}", f"新签名: {new.signature}"])
                    for old, new in diff.changed
                ],
            ),
        ]
        parts += [f"#### {label}\n\n" + fence("\n\n".join(blocks)) for label, blocks in sections if blocks]
    parts.append(f"新快照在 artifact：\n\n{fence(artifact)}\n原样放到这个路径并提交：\n\n{fence(path)}")
    return "\n".join(parts)


def write(path: str, text: str, mode: str) -> None:
    # Encoded before opening, so text that is not valid UTF-8 never leaves a truncated file behind.
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise Error(f"cannot write {quote(path)}: text is not valid UTF-8 ({quote(error.reason)})") from error
    try:
        with open(path, mode + "b") as file:
            file.write(data)
    except OSError as error:
        raise Error(f"cannot write {quote(path)}: {type(error).__name__}") from error


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Compare generated interface entries with the locked snapshot.")
    parser.add_argument("--entries", required=True, help="the generator's JSON array of entries")
    parser.add_argument("--snapshot", required=True, help="the locked snapshot; may not exist; only read")
    parser.add_argument("--output", required=True, help="where to write the new snapshot on mismatch or missing file")
    parser.add_argument("--artifact", required=True, help="artifact name, shown in the summary")
    parser.add_argument("--path", required=True, help="configured snapshot path, shown in the summary")
    args = parser.parse_args(argv)
    try:
        entries = read(Path(args.entries), "entries")
        snapshot = read_snapshot(Path(args.snapshot))
        diff = None if snapshot is None else compare(snapshot, entries)
        matches = diff is not None and diff.empty
        if not matches:
            write(args.output, render(entries), "w")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            write(summary, summary_text(diff, entries, args.artifact, args.path), "a")
    except Error as error:
        print(f"api snapshot: {error}", file=sys.stderr)
        return 2
    print("\n".join(log_lines(diff, entries, args.artifact, args.path)))
    return 0 if matches else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as error:  # noqa: BLE001 -- anything unexpected means the check could not run.
        print(f"api snapshot: {type(error).__name__}: {quote(str(error))}", file=sys.stderr)
        sys.exit(2)
