#!/usr/bin/env python3
"""Check open-code.sh / open-code.ps1 of a rendered project on fake repositories and builds, for the template's CI.

    tests/check_open_code.py <rendered project> <scratch folder>

Copies the two scripts (and the README, for its "Working in VS Code" line) into fake repositories made in the scratch
folder: a project with its own sources (standalone csCMake layout in _csw and in a folder of another name, a venv), a library checked out by its project
(sibling layout), a repository without csCMake sources, an env layout without its variable. The builds are fake
CMakeCache.txt files: Ninja Multi-Config (gcc), Ninja single-config with Catch2 (clang), Visual Studio (clang-cl). A
stub `code` first on the PATH records how VS Code would be opened. Checks the workspace files the scripts write.
On Linux and macOS: open-code.sh (bash; bash 3.2 on macOS); on Windows: open-code.ps1 with PowerShell 7 and Windows
PowerShell 5.1.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == "nt"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def cmake_path(path: Path) -> str:
    """A path as CMake writes it in its cache (forward slashes)."""
    return str(path).replace("\\", "/")


def same_path(a: str, b: Path) -> bool:
    norm_a = a.replace("\\", "/").rstrip("/")
    norm_b = cmake_path(b).rstrip("/")
    return norm_a.lower() == norm_b.lower() if WINDOWS else norm_a == norm_b


def fake_cache(build: Path, source: Path, compiler: str, generator: str, configurations: str, **extra: str) -> None:
    lines = {
        "CMAKE_HOME_DIRECTORY:INTERNAL": cmake_path(source),
        "CMAKE_CXX_COMPILER:FILEPATH": compiler,
        "CMAKE_GENERATOR:INTERNAL": generator,
        "CMAKE_COMMAND:INTERNAL": "/usr/bin/cmake",
        "CMAKE_CTEST_COMMAND:INTERNAL": "/usr/bin/ctest",
        "CMAKE_CONFIGURATION_TYPES:STRING": configurations if ";" in configurations else "",
        "CMAKE_BUILD_TYPE:STRING": "" if ";" in configurations else configurations,
    }
    lines.update(extra)
    write(build / "CMakeCache.txt", "".join(f"{key}={value}\n" for key, value in lines.items()))


def make_repository(
    root: Path, name: str, rendered: Path, layout: str | None, venv: bool, standalone_dir: str = "_csw"
) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    for file in ("open-code.sh", "open-code.ps1", "README.md"):
        shutil.copy(rendered / file, repo / file)
    if layout is not None:
        write(repo / "CMakeLists.txt", "cmake_minimum_required(VERSION 3.25)\n")
        include = {"standalone": f"${{sourceDir}}/{standalone_dir}/csCMake", "env": "$penv{CSCMAKE_ROOT}"}.get(
            layout.split("-")[0], "${sourceParentDir}/csCMake"
        )
        write(
            repo / "csCMakeProjectSetup.json",
            json.dumps({"version": 10, "include": [f"{include}/presets/layout/{layout}.json"]}) + "\n",
        )
    if venv:
        write(repo / ".venv" / ("Scripts/Activate.ps1" if WINDOWS else "bin/activate"), "\n")
    return repo


class Runner:
    def __init__(self, scratch: Path) -> None:
        self.stub_dir = scratch / "stub"
        self.log = scratch / "code.log"
        if WINDOWS:
            write(self.stub_dir / "code.cmd", '@echo %* >> "%CODE_LOG%"\n')
        else:
            write(self.stub_dir / "code", '#!/bin/sh\necho "$*" >> "$CODE_LOG"\n')
            (self.stub_dir / "code").chmod(0o755)
        self.shells = (
            [["pwsh", "-NoProfile", "-File"], ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]]
            if WINDOWS
            else [["bash"]]
        )

    def run(self, shell: list[str], repo: Path, *args: str, env_extra: dict[str, str] | None = None) -> tuple[int, str]:
        script = repo / ("open-code.ps1" if WINDOWS else "open-code.sh")
        env = dict(os.environ)
        env["PATH"] = str(self.stub_dir) + os.pathsep + env["PATH"]
        env["CODE_LOG"] = str(self.log)
        env.pop("CSCMAKE_WORKSPACE_ROOT", None)
        env.update(env_extra or {})
        self.log.unlink(missing_ok=True)
        result = subprocess.run([*shell, str(script), *args], check=False, env=env, capture_output=True, text=True)
        output = result.stdout + result.stderr
        print(f"$ {' '.join(shell)} {script.name} {' '.join(args)} -> {result.returncode}")
        print("  " + output.strip().replace("\n", "\n  "))
        return result.returncode, output

    def opened(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace").strip() if self.log.exists() else ""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAILED: {message}")


def load(path: Path) -> dict:  # type: ignore[type-arg]
    check(path.is_file(), f"{path} written")
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text)  # type: ignore[no-any-return]


def check_shell(runner: Runner, shell: list[str], scratch: Path, rendered: Path) -> None:
    debugger = "cppvsdbg" if WINDOWS else "cppdbg"

    # a project with its own sources (standalone layout, a venv)
    root = scratch / "standalone"
    shutil.rmtree(root, ignore_errors=True)
    app = make_repository(root, "app", rendered, "standalone", venv=True)
    builds = app / "_csw" / "build"

    code, _ = runner.run(shell, app)
    check(code == 0, "no build yet: exit 0")
    python_only = load(app / "_csw" / "app.code-workspace")
    check(python_only["settings"]["clangd.enable"] is False, "Python-only window: clangd off")
    check(
        python_only["settings"]["cmake.configureOnOpen"] is False, "Python-only window: CMake Tools does not configure"
    )
    check("app.code-workspace" in runner.opened(), "Python-only window opened")

    gcc = builds / "v1gcc" / "app"
    fake_cache(gcc, app, "/usr/bin/g++", "Ninja Multi-Config", "Debug;Release;Paranoid")
    single = builds / "v1clang" / "app" / "debug"
    fake_cache(single, app, "/usr/bin/clang++", "Ninja", "Debug", **{"Catch2_DIR:PATH": "/x/Catch2"})
    msvc = builds / "v1msvc" / "app"
    fake_cache(
        msvc,
        app,
        "C:/VS/bin/clang-cl.exe",
        "Visual Studio 17 2022",
        "Debug;Release",
        **{"CMAKE_GENERATOR_PLATFORM:INTERNAL": "x64", "CMAKE_GENERATOR_TOOLSET:INTERNAL": "ClangCL"},
    )

    code, output = runner.run(shell, app)
    check(code == 1 and "Several builds" in output, "several builds, no filter: lists them, exit 1")
    code, output = runner.run(shell, app, "nomatch")
    check(code != 0 and "No build of app" in output, "a filter matching nothing: exit != 0")

    code, output = runner.run(shell, app, "v1gcc")
    check(code == 0, "gcc build: exit 0")
    ws = load(gcc / "app.code-workspace")
    settings = ws["settings"]
    check(same_path(settings["cmake.buildDirectory"], gcc), "gcc: build directory")
    check(same_path(settings["cmake.sourceDirectory"], app), "gcc: source directory")
    check(settings["cmake.generator"] == "Ninja Multi-Config", "gcc: generator")
    variants = settings["cmake.defaultVariants"]["buildType"]
    check(list(variants["choices"]) == ["Debug", "Release", "Paranoid"], "gcc: the configurations of the build")
    check(variants["default"] == "Debug", "gcc: Debug by default")
    check(settings["clangd.enable"] is True and settings["C_Cpp.intelliSenseEngine"] == "disabled", "gcc: clangd")
    check(
        any(
            same_path(a.split("=", 1)[1], gcc)
            for a in settings["clangd.arguments"]
            if a.startswith("--compile-commands-dir=")
        ),
        "gcc: compile database",
    )
    check("python.defaultInterpreterPath" in settings, "gcc: the venv's Python")
    names = [c["name"] for c in ws["launch"]["configurations"]]
    check(names == ["Debug the CTest test", "Debug the launch target"], f"gcc: launch configurations {names}")
    check(all(c["type"] == debugger for c in ws["launch"]["configurations"]), f"gcc: debugger {debugger}")
    check("--goto" in runner.opened() and "README.md:" in runner.opened(), "gcc: opened with the README section")

    code, _ = runner.run(shell, app, "v1clang")
    check(code == 0, "single-config build: exit 0")
    ws = load(single / "app.code-workspace")
    check(same_path(ws["settings"]["cmake.buildDirectory"], single), "single-config: build directory")
    check(
        list(ws["settings"]["cmake.defaultVariants"]["buildType"]["choices"]) == ["Debug"], "single-config: Debug only"
    )
    names = [c["name"] for c in ws["launch"]["configurations"]]
    check("Debug the launch target (Catch2 test spec)" in names, "Catch2: its configuration")
    check([i["id"] for i in ws["launch"]["inputs"]] == ["catch2TestSpec"], "Catch2: its input")
    check("v1clang/debug" in ws["folders"][0]["name"], "single-config: label variant/config")

    code, _ = runner.run(shell, app, "v1msvc")
    check(code == 0, "Visual Studio build: exit 0")
    settings = load(msvc / "app.code-workspace")["settings"]
    check(settings["clangd.enable"] is False, "Visual Studio: clangd off (no compile database)")
    check(settings["C_Cpp.intelliSenseEngine"] == "default", "Visual Studio: cpptools IntelliSense")
    check(
        settings["C_Cpp.default.configurationProvider"] == "ms-vscode.cmake-tools",
        "Visual Studio: CMake Tools provider",
    )
    check(
        settings["cmake.platform"] == "x64" and settings["cmake.toolset"] == "ClangCL",
        "Visual Studio: platform, toolset",
    )

    # a library checked out by its project (sibling layout, no venv)
    root = scratch / "sibling"
    shutil.rmtree(root, ignore_errors=True)
    lib = make_repository(root / "workspace", "lib", rendered, "sibling", venv=False)
    code, output = runner.run(shell, lib)
    check(code != 0 and "No build of lib" in output, "library without a build: exit != 0")
    lib_build = root / "workspace" / "build" / "v1gcc" / "lib"
    fake_cache(lib_build, lib, "/usr/bin/g++", "Ninja Multi-Config", "Debug;Release")
    code, _ = runner.run(shell, lib)
    check(code == 0, "library: its single build without a filter")
    check("python.defaultInterpreterPath" not in load(lib_build / "lib.code-workspace")["settings"], "library: no venv")

    # the standalone folder is the one of the include, whatever its name: none is written in the scripts
    root = scratch / "anyname"
    shutil.rmtree(root, ignore_errors=True)
    other = make_repository(root, "other", rendered, "standalone-short", venv=False, standalone_dir="any/where")
    other_build = other / "any" / "where" / "build" / "v1gcc" / "other"
    fake_cache(other_build, other, "/usr/bin/g++", "Ninja Multi-Config", "Debug")
    code, _ = runner.run(shell, other)
    check(code == 0 and (other_build / "other.code-workspace").is_file(), "standalone: the folder of the include")

    # a repository without csCMake sources (a tool, a project without C++ of its own)
    root = scratch / "plain"
    shutil.rmtree(root, ignore_errors=True)
    tool = make_repository(root, "tool", rendered, None, venv=True)
    code, _ = runner.run(shell, tool)
    check(code == 0, "no csCMake sources: exit 0")
    opened = runner.opened()
    check(opened != "" and "code-workspace" not in opened, "no csCMake sources: the folder opened")

    # the env layout needs CSCMAKE_WORKSPACE_ROOT
    root = scratch / "env"
    shutil.rmtree(root, ignore_errors=True)
    env_repo = make_repository(root, "envrepo", rendered, "env", venv=False)
    code, output = runner.run(shell, env_repo)
    check(code != 0 and "CSCMAKE_WORKSPACE_ROOT" in output, "env layout without its variable: exit != 0")
    env_build = root / "elsewhere" / "build" / "v1gcc" / "envrepo"
    fake_cache(env_build, env_repo, "/usr/bin/g++", "Ninja Multi-Config", "Debug")
    code, _ = runner.run(shell, env_repo, env_extra={"CSCMAKE_WORKSPACE_ROOT": str(root / "elsewhere")})
    check(
        code == 0 and (env_build / "envrepo.code-workspace").is_file(),
        "env layout: the build in CSCMAKE_WORKSPACE_ROOT",
    )


def main() -> int:
    rendered, scratch = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    scratch.mkdir(parents=True, exist_ok=True)
    scratch = scratch.resolve()
    runner = Runner(scratch)
    for shell in runner.shells:
        print(f"=== {shell[0]}")
        check_shell(runner, shell, scratch, rendered)
    print("open-code: every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
