"""Launcher used by the /reposage commands.

    python3 bootstrap.py scan [PATH] [--full]

It makes sure RepoSage's own private virtual environment exists, then runs
`reposage` inside it with the same arguments.

Why a private environment? Tree-sitter is a compiled package. Installing it
into the user's own Python could clash with their projects, or fail because
of permissions. So we keep everything in ~/.reposage/venv (change the
location with the REPOSAGE_HOME environment variable).

This file deliberately uses ONLY the Python standard library, because it has
to run before any dependencies are installed.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = os.path.join(PLUGIN_ROOT, "requirements-plugin.txt")
HOME = os.environ.get("REPOSAGE_HOME") or os.path.join(os.path.expanduser("~"), ".reposage")
VENV = os.path.join(HOME, "venv")
STAMP = os.path.join(VENV, ".reposage-ready")   # written LAST = setup finished
LOCK = os.path.join(HOME, "setup.lock")

MIN_PYTHON = (3, 10)          # required by the tree-sitter packages
CHECK_IMPORTS = ("tree_sitter", "tree_sitter_python",
                 "tree_sitter_javascript", "tree_sitter_typescript",
                 "tree_sitter_java")


def say(msg: str) -> None:
    """Progress messages go to stderr so they never mix with real output."""
    print(msg, file=sys.stderr, flush=True)


def fail(msg: str) -> None:
    say(f"\nRepoSage setup failed: {msg}")
    sys.exit(3)


# ----------------------------------------------------------------------
# Finding things
# ----------------------------------------------------------------------

def venv_python() -> str:
    if os.name == "nt":
        return os.path.join(VENV, "Scripts", "python.exe")
    return os.path.join(VENV, "bin", "python")


def wanted_stamp(base_python: str) -> str:
    """Fingerprint of what the environment should contain. If the
    requirements change (plugin update), the environment is rebuilt."""
    with open(REQUIREMENTS, "rb") as f:
        reqs = f.read()
    real = os.path.realpath(base_python)   # same Python via any symlink
    return hashlib.sha256(reqs + real.encode()).hexdigest()


def is_ready(base_python: str) -> bool:
    try:
        with open(STAMP, encoding="utf-8") as f:
            ok = f.read().strip() == wanted_stamp(base_python)
    except OSError:
        return False
    # The stamp can survive while Python itself was removed/upgraded.
    return ok and os.path.exists(venv_python())


def find_base_python() -> str:
    """A Python >= 3.10 to build the environment with (prefer the current one)."""
    if sys.version_info[:2] >= MIN_PYTHON:
        return sys.executable
    for minor in (13, 12, 11, 10):
        exe = shutil.which(f"python3.{minor}")
        if exe:
            return exe
    fail(f"RepoSage needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer, "
         f"but this is Python {sys.version.split()[0]}.\n"
         f"Install a newer Python from https://www.python.org/downloads/ "
         f"and run the command again.")
    return ""  # unreachable


# ----------------------------------------------------------------------
# Setting up the environment
# ----------------------------------------------------------------------

def run_quiet(cmd: list[str], what: str) -> None:
    """Run a setup step; on failure show its output so the user can fix it."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        output = (proc.stdout + proc.stderr).strip()
        hint = ""
        if "ensurepip" in output or "No module named venv" in output:
            hint = ("\nHint: your Python is missing the 'venv' module. On Debian/Ubuntu "
                    "run:  sudo apt install python3-venv")
        elif "pip install" in what and ("Could not find" in output or "connection" in output.lower()):
            hint = ("\nHint: RepoSage downloads its parser packages from PyPI once. "
                    "Check your internet connection or proxy settings.")
        fail(f"could not {what}.\n--- output ---\n{output[-3000:]}{hint}")


def setup(base_python: str) -> None:
    os.makedirs(HOME, exist_ok=True)
    with SetupLock():
        if is_ready(base_python):         # another process finished it meanwhile
            return
        rebuilding = os.path.isdir(VENV)
        say("RepoSage: dependencies changed, updating its environment (~1 min)..."
            if rebuilding else
            "First run: setting up RepoSage (~1 min). This only happens once.")
        say(f"  -> creating a private Python environment in {VENV}")
        shutil.rmtree(VENV, ignore_errors=True)
        run_quiet([base_python, "-m", "venv", VENV], "create the virtual environment")

        say("  -> installing Tree-sitter parsers (Python, JavaScript, TypeScript, Java)")
        py = venv_python()
        run_quiet([py, "-m", "pip", "install", "--disable-pip-version-check",
                   "--no-input", "--quiet", "-r", REQUIREMENTS],
                  "pip install the parser packages")

        say("  -> checking the installation")
        run_quiet([py, "-c", "import " + ", ".join(CHECK_IMPORTS)],
                  "import the installed packages")

        with open(STAMP, "w", encoding="utf-8") as f:
            f.write(wanted_stamp(base_python))
        say("RepoSage is ready.\n")


class SetupLock:
    """Stops two scans from building the environment at the same time.
    mkdir is atomic on every OS, so it works as a simple lock."""

    def __enter__(self) -> "SetupLock":
        deadline = time.time() + 600
        while True:
            try:
                os.mkdir(LOCK)
                return self
            except FileExistsError:
                # A lock older than 15 minutes is left over from a crash.
                try:
                    if time.time() - os.path.getmtime(LOCK) > 900:
                        os.rmdir(LOCK)
                        continue
                except OSError:
                    continue
                if time.time() > deadline:
                    fail(f"timed out waiting for another setup to finish "
                         f"(delete {LOCK} if no other RepoSage is running).")
                say("RepoSage: waiting for another setup to finish...")
                time.sleep(3)

    def __exit__(self, *exc) -> None:
        try:
            os.rmdir(LOCK)
        except OSError:
            pass


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> int:
    base_python = find_base_python()
    if not is_ready(base_python):
        setup(base_python)

    # -I (isolated mode) ignores PYTHONPATH, user site-packages and the
    # current folder, so the user's own files can never shadow our package.
    # The working directory is kept, so relative paths like "." still work.
    launcher = ("import sys; sys.path.insert(0, %r); "
                "from reposage.__main__ import main; sys.exit(main())" % PLUGIN_ROOT)
    try:
        return subprocess.call([venv_python(), "-I", "-c", launcher, *sys.argv[1:]])
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
