#!/usr/bin/env python3

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
URLS_PATH = ROOT / "urls" / "all-urls.txt"
ADD_CONFIG_ENTRY_PATH = ROOT / "add_config_entry.py"


def prompt_action(prompt):
    while True:
        answer = input(f"{prompt} [Y/n/q]: ").strip().lower()
        if not answer:
            return "yes"
        if answer in {"y", "yes"}:
            return "yes"
        if answer in {"n", "no"}:
            return "no"
        if answer in {"q", "quit"}:
            return "quit"
        print("Please answer y, n, or q.")


def get_first_url(path):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    for index, line in enumerate(lines):
        if line.strip():
            return index, line.strip()

    return None


def remove_line(path, index_to_remove):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if 0 <= index_to_remove < len(lines):
        remaining_lines = lines[:index_to_remove] + lines[index_to_remove + 1 :]
        path.write_text("".join(remaining_lines), encoding="utf-8")


def main():
    argparse.ArgumentParser(
        description=(
            "Process URLs from urls/all-urls.txt one by one, optionally pass "
            "each to add_config_entry.py, and remove processed URLs. Enter q "
            "to stop without consuming the current URL."
        )
    ).parse_args()

    if not URLS_PATH.exists():
        print(f"URL file not found: {URLS_PATH}", file=sys.stderr)
        return 1

    while True:
        first_url = get_first_url(URLS_PATH)
        if first_url is None:
            print("No URLs left in urls/all-urls.txt.")
            return 0

        index, url = first_url
        print(f"Topmost URL: {url}")

        try:
            action = prompt_action("Add this URL to the config?")
        except (EOFError, KeyboardInterrupt):
            print("")
            print("Stopped without consuming the current URL.")
            return 0

        if action == "quit":
            print("Stopped without consuming the current URL.")
            return 0

        should_consume = False

        try:
            if action == "no":
                print("Skipped adding config entry.")
                should_consume = True
            else:
                result = subprocess.run(
                    [sys.executable, str(ADD_CONFIG_ENTRY_PATH), url]
                )
                if result.returncode != 0:
                    print(
                        f"add_config_entry.py exited with code {result.returncode}.",
                        file=sys.stderr,
                    )
                else:
                    should_consume = True
        except KeyboardInterrupt:
            print("")
            print("Stopped without consuming the current URL.")
            return 0
        finally:
            if should_consume:
                remove_line(URLS_PATH, index)
            print("")


if __name__ == "__main__":
    raise SystemExit(main())
