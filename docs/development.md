# Development and builds

See the [README](../README.md) for installation and usage.
This document covers Python development, tests, and builds.

## Dependencies

Python 3.11–3.14. From the repository root:

```sh
python -m pip install --only-binary=:all: -r requirements.lock
python -m pip install --no-build-isolation --no-deps -e .
```

Exact dependency versions are pinned in `requirements.lock`.

## Checks

```sh
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build --no-isolation
```

The main tests run the real `zoo-kcl==0.3.191` backend through the CLI. Fixtures
explicitly select KCL 3.0. Test doubles reproduce failures: hangs, unexpected
exits, native panics, and incomplete reports.

On macOS, offline operation is tested separately:

```sh
python -m pytest tests/test_offline.py
```

The test first verifies that macOS blocks network sockets, then runs all four
commands under the same restriction without Zoo tokens. Mocking Python's `socket`
is not a substitute for this test. If an outer sandbox prohibits `sandbox_apply`,
the test is skipped with an explanation; run it from a regular terminal.

## Building the Python package

```sh
python -m build --no-isolation
```

Output: wheel and sdist in `dist/`.

For manual builds, versions in `pyproject.toml` and `src/zoo_kcl_check_cli/__init__.py` must match.

## Publishing to PyPI

Specify the version component to bump: `patch`, `minor`, or `major`. This argument is required.

```sh
python scripts/publish.py --bump patch --dry-run
python scripts/publish.py --bump patch
```

The script checks that versions match and bumps them in `pyproject.toml` and `__init__.py`.
`patch`: `0.1.2 → 0.1.3`, `minor`: `0.1.2 → 0.2.0`, `major`: `0.1.2 → 1.0.0`.
`--dry-run` checks and builds the new version without uploading, then restores
the original contents of both files, including on failure.
The script runs ruff and Python package tests, builds the wheel and sdist in a new
`dist/publish-VERSION-*` directory, and passes only those two files to `uv publish`.
Any error stops publication. Standalone executable tests run separately after
building the executable.

Requires the dependencies from `requirements.lock`, the installed project, and `uv`.
Authentication follows standard `uv publish` behavior: an interactive token prompt
or `UV_PUBLISH_TOKEN` in the environment. The script does not store the token.
During a normal run, the new version remains in the files even if checks or upload
fail; remember that running again with `--bump` will increase it again.

## Building the standalone executable

```sh
python -m PyInstaller --clean --noconfirm packaging/kcl-check.spec
./dist/kcl-check --help
python -m pytest tests/test_binary.py
```

Output: `dist/kcl-check` on macOS/Linux or `dist/kcl-check.exe` on Windows.
It includes Python and the native CAD library. Build on the target OS
and architecture.

Executable tests check the bundled backend, worker, imports, and JSON output from
another directory with a clean Python environment. They are skipped until the
executable is built.

The managed Codex sandbox on macOS may prohibit `semctl` and nested `sandbox_apply`;
run acceptance tests from a regular terminal. If the default PyInstaller cache
is not writable, set a custom directory with `PYINSTALLER_CONFIG_DIR`.

## Result contract

`--json` writes one object to stdout, including for input errors and timeouts.
Main fields: `schema_version`, `command`, `ok`, `exit_code`, `mode`, `backend`,
`geometry_validated`, `targets`, `diagnostics`, `sketches`, `formatting`.

`geometry_validated` is always `false`. An unknown command produces `command: null`
and `mode: null`. Help and version remain text. Modes: `local_format`,
`local_lint`, `local_mock_execution`, `local_sketch_constraints`.

Diagnostics preserve the library's full report in `rendered`. `file` and `range`
may be `null`: the CLI does not guess error locations in imported modules.
For known ranges, lines and columns are one-based, the end is exclusive, and
columns count Unicode characters. The linter's UTF-8 byte offsets are converted
to these ranges.

The sketch report contains `complete`, `total`, and `entries`. Each entry has
`name`, `instance_index`, `status`, `free_count`, `conflict_count`, `total_count`.
The instance index is zero-based. Counts describe sketch segments. A complete
empty report succeeds; a partial report is preserved alongside the error and
does not count as success.

`formatting` contains `file`, `changed`, `written`, `formatted_code`. Code is
included only when formatting without `--write`/`--check`. Formatting for all files
is prepared before writing: a read or parse error cancels the batch's writes.
Each file replacement is atomic, preserves permissions, and leaves unchanged
files untouched. On a write error or timeout, files already written are marked
`written: true`.

## Architecture and dependencies

- `cli.py`: arguments, help, and output.
- `core.py`: paths, project traversal, and writing formatted files.
- `backend.py`: permitted Zoo KCL calls and result normalization.
- `runner.py`: a separate process using `spawn`, timeout, and interruption cleanup.
- `models.py`: requests, results, and diagnostics.

The native library is imported only in the worker. `ZOO_API_TOKEN` and
`KITTYCAD_API_TOKEN` are removed before import. The CLI does not read Zoo
credentials/configuration, invoke cloud execution, or fall back to cloud execution.

CLI acceptance testing was performed on macOS ARM64 with Python 3.13. Prebuilt
`zoo-kcl==0.3.191` wheels are available for Python 3.11–3.14, but not Python 3.15.
Full CLI acceptance testing has not been performed on Windows or Linux.

## API references

- [Zoo KCL Python bindings](https://github.com/KittyCAD/modeling-app/blob/main/rust/kcl-python-bindings/src/lib.rs).
- [zoo-kcl 0.3.191](https://pypi.org/project/zoo-kcl/0.3.191/).
- [PyInstaller onefile](https://pyinstaller.org/en/stable/operating-mode.html).
- [PyInstaller multiprocessing](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing).
