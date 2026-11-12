import sys
import tomllib
from pathlib import Path

# config_overrides.py lives in a sibling directory of the same checkout and holds the shared TOML writer.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "config-overrides"))
from config_overrides import Error, dump_toml


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: deny_merge.py SHARED_DENY_TOML LICENSES_TOML OUTPUT", file=sys.stderr)
        return 2
    shared_path, licenses_path, output = (Path(a) for a in argv)
    try:
        shared = tomllib.loads(shared_path.read_text())
        # licenses.toml's allow is the default; an allow the caller set in deny.toml's own section wins.
        shared.setdefault("licenses", {}).setdefault("allow", tomllib.loads(licenses_path.read_text())["allow"])
        output.write_text(dump_toml(shared))
    except (OSError, KeyError, tomllib.TOMLDecodeError, Error) as error:
        print(f"deny_merge: {error!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
