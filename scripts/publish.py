#!/usr/bin/env python3
"""Check, build, and publish the current release to PyPI."""

import argparse
import ast
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def release_info(root: Path) -> tuple[str, str]:
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    module = ast.parse((root / "src/zoo_kcl_check_cli/__init__.py").read_text())
    versions = [
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    ]
    version = project["version"]
    if versions != [version]:
        raise ValueError("Versions in pyproject.toml and __init__.py must match")
    return project["name"], version


def run(*command: str) -> None:
    print(f"$ {shlex.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def bumped_version(version: str, part: str) -> str:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("--bump requires a stable MAJOR.MINOR.PATCH version")
    major, minor, patch = map(int, version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def update_versions(root: Path, old: str, new: str) -> dict[Path, bytes]:
    project_path = root / "pyproject.toml"
    module_path = root / "src/zoo_kcl_check_cli/__init__.py"
    originals = {path: path.read_bytes() for path in (project_path, module_path)}
    project = originals[project_path].decode("utf-8")
    # Restrict replacement to [project], leaving other TOML tables untouched.
    section = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", project)
    if section is None:
        raise ValueError("Cannot find [project] in pyproject.toml")
    replacements = {}
    for path, source, field in (
        (project_path, section[1], "version"),
        (module_path, originals[module_path].decode("utf-8"), "__version__"),
    ):
        pattern = rf"(?m)^({field}\s*=\s*)([\"']){re.escape(old)}\2"
        updated, count = re.subn(
            pattern, lambda match: f"{match[1]}{match[2]}{new}{match[2]}", source
        )
        if count != 1:
            raise ValueError(f"Cannot update {field} in {path.name}")
        if path == project_path:
            updated = project[: section.start(1)] + updated + project[section.end(1) :]
        replacements[path] = updated.encode("utf-8")
    try:
        for path, content in replacements.items():
            path.write_bytes(content)
    except OSError:
        for path, content in originals.items():
            path.write_bytes(content)
        raise
    return originals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bump",
        required=True,
        choices=("patch", "minor", "major"),
        help="version component to bump",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check and build the next version without keeping changes or uploading",
    )
    args = parser.parse_args(argv)
    name, current = release_info(ROOT)
    version = bumped_version(current, args.bump)
    if not args.dry_run and shutil.which("uv") is None:
        raise ValueError("uv is required for publishing")
    print(f"Release: {name} {current} -> {version}", flush=True)
    originals = update_versions(ROOT, current, version)
    try:
        return publish_release(name, version, args.dry_run)
    finally:
        if args.dry_run:
            for path, content in originals.items():
                path.write_bytes(content)


def publish_release(name: str, version: str, dry_run: bool) -> int:
    run(sys.executable, "-m", "ruff", "check", ".")
    run(sys.executable, "-m", "ruff", "format", "--check", ".")
    # Standalone executable acceptance is separate from the PyPI package release.
    run(sys.executable, "-m", "pytest", "--ignore=tests/test_binary.py")

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=f"publish-{version}-", dir=dist))
    run(sys.executable, "-m", "build", "--no-isolation", "--outdir", str(output))
    normalized_name = re.sub(r"[-_.]+", "_", name).lower()
    wheel = output / f"{normalized_name}-{version}-py3-none-any.whl"
    sdist = output / f"{normalized_name}-{version}.tar.gz"
    if not wheel.is_file() or not sdist.is_file():
        raise ValueError(f"Expected wheel and sdist were not built in {output}")
    print(f"Artifacts: {output}", flush=True)
    if dry_run:
        print("Dry run complete; nothing uploaded.")
    else:
        run("uv", "publish", str(wheel), str(sdist))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
