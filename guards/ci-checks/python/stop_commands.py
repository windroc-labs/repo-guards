"""Shared by the Python wrappers whose reports quote the PR's file names and source lines."""

import os
import secrets
import sys
import traceback
from collections.abc import Callable


def run_quoted(check: Callable[[], int]) -> int:
    """Runs `check` with stdout and stderr inside a ::stop-commands:: block, so no line acts as a workflow command.

    An exception prints its traceback inside the block, since it can quote the PR's text, and returns 2.
    """
    token = secrets.token_hex(16)
    print(f"::stop-commands::{token}", flush=True)
    sys.stderr.flush()
    os.dup2(sys.stdout.fileno(), sys.stderr.fileno())
    try:
        return check()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        print(f"::{token}::", flush=True)
