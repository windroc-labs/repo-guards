import json
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


FIELDS = frozenset(Entry._fields)


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


def quote_key(key: Key) -> str:
    return "(" + ", ".join(quote(part) for part in key) + ")"


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
        key = entry[:4]
        if key in entries:
            raise Error(f"{source}: duplicate key (unit, parent, kind, name) {quote_key(key)} at item {index}")
        entries[key] = entry
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
