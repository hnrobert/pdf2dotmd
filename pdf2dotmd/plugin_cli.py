"""``pdf2dotmd plugin`` subcommand — manage optional conversion backends.

Subcommands:

* ``list`` — show known and installed backends
* ``install <name>`` — install a backend's optional dependencies via pip
* ``uninstall <name>`` — remove a backend's optional dependencies
* ``info <name>`` — show details about a backend

Install/uninstall shell out to ``pip`` in the current interpreter. When run
under pipx, the correct ``pipx inject`` / ``pipx runpip`` guidance is printed
instead (shelling out there would target the wrong environment).
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys

from .plugins import KNOWN_BACKENDS, discover_backends

logger = logging.getLogger(__name__)


class PluginCli:
    """Argument parser and dispatch for the ``plugin`` subcommand."""

    def run(self, argv: list[str]) -> int:
        parser = argparse.ArgumentParser(
            prog="pdf2dotmd plugin",
            description="Manage optional pdf2dotmd conversion backends.",
        )
        subparsers = parser.add_subparsers(dest="plugin_command", required=True)

        subparsers.add_parser("list", help="List available and installed backends")

        install = subparsers.add_parser("install", help="Install an optional backend")
        install.add_argument("name", help="Backend name (e.g. docling)")
        install.add_argument(
            "pip_args",
            nargs=argparse.REMAINDER,
            help="Extra arguments forwarded to pip (after --)",
        )

        uninstall = subparsers.add_parser("uninstall", help="Uninstall an optional backend")
        uninstall.add_argument("name", help="Backend name")

        info = subparsers.add_parser("info", help="Show details about a backend")
        info.add_argument("name", help="Backend name")

        args = parser.parse_args(argv)

        dispatch = {
            "list": self._list,
            "install": self._install,
            "uninstall": self._uninstall,
            "info": self._info,
        }
        return dispatch[args.plugin_command](args)

    def _list(self, _args) -> int:
        backends = discover_backends()
        if not backends:
            print("No backends found.")
            return 0

        print("Available backends:")
        name_width = max(len(name) for name in backends)
        for name, info in sorted(backends.items()):
            status = "installed" if info.installed else "not installed"
            description = f"  {info.description}" if info.description else ""
            print(f"  {name:<{name_width}}  [{status}]{description}")
            if info.install_spec and not info.installed:
                print(f"      install: pdf2dotmd plugin install {name}")
        return 0

    def _install(self, args) -> int:
        name = args.name
        meta = KNOWN_BACKENDS.get(name)
        if meta is None:
            print(f"Unknown backend: {name}")
            self._print_known()
            return 1

        min_python = meta.get("min_python")
        if min_python and sys.version_info[:2] < min_python:
            print(
                f"Backend '{name}' requires Python >={min_python[0]}.{min_python[1]}. "
                f"You have {_py_version()}."
            )
            return 1

        extra_args = self._strip_leading_dashdash(args.pip_args or [])
        return _pip_install(meta["install_spec"], extra_args)

    def _uninstall(self, args) -> int:
        name = args.name
        meta = KNOWN_BACKENDS.get(name)
        if meta is None:
            print(f"Unknown backend: {name}")
            self._print_known()
            return 1

        targets = meta.get("uninstall_targets") or [name]
        if _running_under_pipx():
            cmd = f"pipx runpip pdf2dotmd uninstall -y {' '.join(targets)}"
            print(
                "pdf2dotmd is installed via pipx. Remove the backend with:\n"
                f"  {cmd}"
            )
            return 0

        cmd = [sys.executable, "-m", "pip", "uninstall", "-y", *targets]
        print("Removing:", " ".join(shlex.quote(c) for c in cmd))
        proc = subprocess.run(cmd, check=False)
        print(
            "\nNote: pip does not remove transitive dependencies, so some ML "
            "packages may remain. Remove them manually if needed."
        )
        return proc.returncode

    def _info(self, args) -> int:
        name = args.name
        meta = KNOWN_BACKENDS.get(name)
        info = discover_backends().get(name)

        if meta is None and info is None:
            print(f"Unknown backend: {name}")
            self._print_known()
            return 1

        print(f"Backend: {name}")
        print(f"  installed     : {info.installed if info else False}")
        if meta:
            print(f"  install spec  : {meta.get('install_spec', '-')}")
            print(f"  description   : {meta.get('description', '-')}")
            min_python = meta.get("min_python")
            if min_python:
                print(f"  requires Python: >={min_python[0]}.{min_python[1]}")
        elif info:
            print(f"  source        : {info.source}")
        return 0

    @staticmethod
    def _strip_leading_dashdash(args: list[str]) -> list[str]:
        return args[1:] if args and args[0] == "--" else args

    @staticmethod
    def _print_known() -> None:
        known = ", ".join(sorted(KNOWN_BACKENDS)) or "(none)"
        print(f"Known backends: {known}")


def _running_under_pipx() -> bool:
    """Detect whether pdf2dotmd is running inside a pipx venv."""
    return "pipx" in (sys.executable or "").lower() or bool(os.environ.get("PIPX_HOME"))


def _py_version() -> str:
    return ".".join(str(v) for v in sys.version_info[:3])


def _pip_install(spec: str, extra_args: list[str]) -> int:
    if _running_under_pipx():
        print(
            "pdf2dotmd is installed via pipx. Install the backend with:\n"
            f"  pipx inject pdf2dotmd {spec}"
        )
        return 0

    cmd = [sys.executable, "-m", "pip", "install", spec, *extra_args]
    print("Installing:", " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        manual = " ".join(shlex.quote(c) for c in cmd)
        print(f"\nInstallation failed (exit {proc.returncode}). Try manually:\n  {manual}")
    return proc.returncode
