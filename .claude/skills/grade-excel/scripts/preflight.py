from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from typing import Any

PACKAGE_NAME = "openpyxl"


def _python_command() -> str:
    return sys.executable or "python"


def _module_installed(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _run_install(module_name: str) -> dict[str, Any]:
    command = [_python_command(), "-m", "pip", "install", module_name]
    completed = subprocess.run(command, capture_output=True, text=True)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _status_payload() -> dict[str, Any]:
    return {
        "python_executable": _python_command(),
        "python_version": sys.version.split()[0],
        "openpyxl_installed": _module_installed(PACKAGE_NAME),
    }


def check() -> int:
    print(json.dumps({"ok": _module_installed(PACKAGE_NAME), **_status_payload()}, ensure_ascii=False, indent=2))
    return 0


def ensure() -> int:
    if _module_installed(PACKAGE_NAME):
        print(json.dumps({"ok": True, "action": "none", **_status_payload()}, ensure_ascii=False, indent=2))
        return 0

    install_result = _run_install(PACKAGE_NAME)
    success = install_result["returncode"] == 0 and _module_installed(PACKAGE_NAME)
    payload = {
        "ok": success,
        "action": "install",
        **_status_payload(),
        "install": install_result,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if success else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check and ensure Python dependencies for the grade-excel skill.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Report whether Python and openpyxl are ready")
    subparsers.add_parser("ensure", help="Install openpyxl if it is missing")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "check":
        return check()
    if args.command == "ensure":
        return ensure()

    parser.error(f"Unsupported command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
