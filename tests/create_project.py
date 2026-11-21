#!/usr/bin/env python3
"""Create a project of one kind from this checkout of the template, with a hello world, for the template's CI.

    tests/create_project.py <kind> <destination>

Renders the template at HEAD (the commit under test, not the latest tag) with copier, then adds what a real project
would: a Python package and its tests (tool, project), a C++ source (library), a tests/conftest.py declaring opt-in
markers, and git init. The CI then runs the project's own setup script, pre-commit and pytest in it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent

# tests of the shared conftest.py: the opt-in markers of tests/conftest.py, --run-all, --run=<marker>
CONFTEST = """\
def pytest_optin_markers() -> dict[str, str]:
    return {"slow": "long tests"}


def pytest_manual_markers() -> dict[str, str]:
    return {"smoke": "run by a workflow of its own"}
"""

TEST_MARKERS = '''\
import pytest


@pytest.mark.slow
def test_slow() -> None:
    """Runs with --run-all (CI) or --run=slow."""


@pytest.mark.smoke
def test_smoke() -> None:
    """Never runs with --run-all: the CI checks it is skipped."""
    raise AssertionError("a manual marker ran without --run=smoke")
'''

HELLO_PY = """\
def hello(name: str) -> str:
    return f"hello {name}"
"""

TEST_HELLO = """\
from {module} import hello


def test_hello() -> None:
    assert hello("cscosine") == "hello cscosine"
"""

TEST_CSORCHESTRATOR = '''\
import csorchestrator


def test_csorchestrator_installed() -> None:
    """setup.sh / setup.ps1 installed the pinned csorchestrator."""
    assert csorchestrator.__name__ == "csorchestrator"
'''

# formatted as clang-format leaves it with the template's style: pre-commit must not change it (a CS_OMP block: the
# pragma-like macro once made clang-format drop the indentation of the statement after it)
HELLO_CPP = """\
#include <iostream>

#define CS_OMP(...)

int main() {
  int sum = 0;
CS_OMP(parallel for reduction(+ : sum))
  for (int i = 0; i < 10; i++) {
    sum += i;
  }
  std::cout << "hello cscosine " << sum << "\\n";
  return 0;
}
"""


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    kind, destination = sys.argv[1], Path(sys.argv[2]).resolve()
    name = f"cshello{kind}"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "copier",
            "copy",
            "--defaults",
            "--vcs-ref=HEAD",
            f"--data=project_name={name}",
            f"--data=description=hello world {kind}",
            f"--data=kind={kind}",
            str(TEMPLATE),
            str(destination),
        ],
        check=True,
    )
    if kind == "tool":
        write(destination / "src" / name / "__init__.py", HELLO_PY)
        write(destination / "tests" / "test_hello.py", TEST_HELLO.format(module=name))
    elif kind == "project":
        write(destination / "hello.py", HELLO_PY)
        write(destination / "tests" / "test_hello.py", TEST_HELLO.format(module="hello"))
        write(destination / "tests" / "test_csorchestrator.py", TEST_CSORCHESTRATOR)
    else:
        write(destination / "src" / "hello.cpp", HELLO_CPP)
    if kind != "library":
        write(destination / "tests" / "conftest.py", CONFTEST)
        write(destination / "tests" / "test_markers.py", TEST_MARKERS)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=destination, check=True)
    subprocess.run(["git", "add", "-A"], cwd=destination, check=True)
    # executable in git on every OS: Windows has no executable bit, the rendered scripts would be committed as 644
    scripts = [name for name in ("setup.sh", "open-code.sh") if (destination / name).exists()]
    if scripts:
        subprocess.run(["git", "update-index", "--chmod=+x", *scripts], cwd=destination, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
