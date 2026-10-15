---
name: kcl-check
description: >-
  Install and use the kcl-check CLI for local Zoo / KittyCAD KCL checks: formatting, lint, mock execution, sketch constraints, JSON, and exit codes. Also verify real construction through Zoo CLI at the user's request. Do not use for kcl-lang or development of the CLI implementation.
---

# Checking KCL through the CLI

Use this skill to run checks on a CAD file or project and explain the results.
Rules for writing and changing geometry belong to the `kcl-modeling` skill;
it is not required to run the CLI. If the user only asks to check a model,
report the result; modify source files only within the scope of the user's request.

## Local checks: kcl-check

`kcl-check` checks code and sketch constraints through a mock engine.
After installation, it works without a network connection, account, or tokens.
It does not validate real B-Rep geometry, boolean operations, or fillets:
`geometry_validated` remains `false`.

Check that `kcl-check` is available and run `kcl-check --version`. If the command
is missing from PATH, check the directory returned by `uv tool dir --bin`, if uv
is installed: a working CLI can be invoked by its absolute path without restarting
the shell. Substitute that path for `kcl-check` in the commands below.

If the CLI is not installed, install the published package using an already
available tool manager. Installing a missing `kcl-check` is part of this skill's
workflow; do not stop at reporting that the command was not found:

```sh
pipx install zoo-kcl-check-cli
# Alternative, if using uv:
uv tool install zoo-kcl-check-cli
```

Use one installation method. Python 3.11–3.14 is required; with uv, add
`--python 3.13` to select an interpreter explicitly. Do not reinstall a working CLI.
If no manager or compatible Python is available, ask the user how to install it.
If installation is blocked, explain why and list the checks
that were not performed. Do not substitute cloud checks for unavailable local
checks without the user's permission.

After editing a model, work from the current CAD project's root: first format
each modified `.kcl` file, then run all three project checks:

```sh
kcl-check format main.kcl --write
kcl-check lint . --json
kcl-check execute . --json
kcl-check sketches . --json
```

For a check-only task, use `format --check` instead of `format --write`.
Substitute the names of modified or checked files for `main.kcl`. `lint .` traverses
`.kcl` files recursively; `execute .` and `sketches .` run `default_file` from
`project.toml` or `main.kcl`, including imports. Limit the path to the current project.

- `format` prints formatted code without changing the file. `--write` / `-w`
  writes the result; `--check` only checks formatting and returns `1` if there
  are differences. A directory requires `--write`, `--check`, or `--json`.
- `lint` checks syntax and style without execution; `execute` checks names,
  arguments, types, units, and `assert`; `sketches` checks whether constraints
  fully define sketches and whether they conflict, including all sketch instances.
- Exit code `0` means success; `1` means model findings; `2` means an input or
  environment problem, timeout, or tool failure; `130` means interruption.
  Warnings also cause failure.
- When fixing a model, repeat formatting of modified files and all three project
  checks until they succeed. If the backend is unavailable or the tool fails
  repeatedly, report the reason and unperformed checks instead of retrying indefinitely.
- Preserve the original messages and `rendered`. If `file`/`range` are missing,
  do not invent error locations. The absence of language server diagnostics
  does not prove validity. Investigate editor warnings separately,
  even when the CLI succeeds.

Output is text by default: diagnostics go to stderr, results and reports to stdout.
For programmatic processing, use `--json`: one object in stdout, including input
errors and timeouts. Check `ok`, `exit_code`, `diagnostics`, and the command's report
(`sketches` or `formatting`). `--help` and `--version` remain text.
All four commands support `--timeout SECONDS`; the default is 60 seconds for the
entire command. Consult `kcl-check COMMAND --help` for options.

Under-constrained or conflicting sketches and incomplete reports cause `sketches`
to fail. This does not invalidate a successful result from a separate `execute`
command: it checks execution, not whether constraints fully define sketches.

When working on a CAD model, run the CLI commands on the model itself. The test
suite in the `kcl-check` repository concerns changes to the CLI implementation
and does not replace checks of the CAD project.

## Construction checks: Zoo CLI

Full geometry construction verification from the command line requires **Zoo CLI**
(`zoo`) with the real engine. Its installation and authentication are independent
of `kcl-check`.

If the user requests real geometry verification, check `zoo version`.
If the CLI is missing, offer installation using the
[official instructions](https://zoo.dev/docs/developer-tools/cli/manual#installation)
and ask the user how to install it in their environment. Do not install Zoo CLI
without agreement. Authentication is covered in the same manual (`zoo auth login`).

Cloud checks may consume API credits. Run them at the user's explicit request;
do not ask again for permission already granted. An ordinary request to edit
a model does not, by itself, authorize cloud calls.

Before running a command, check its options with `zoo kcl --help` and the selected
command's help. To check construction and physical properties, you can use:

```sh
zoo kcl analyze main.kcl --format json
```

If the user requests export, use the required format and agreed output directory,
for example `zoo kcl export --output-format step main.kcl output-dir`.
Do not add `--allow-errors` to force a successful exit. Successful construction
or export does not prove compliance with dimensions, intended use, or manufacturing
requirements: verify the requested properties separately. Zoo Design Studio
is an alternative to the CLI for construction checks.

In the result, state which local checks passed and whether real construction
was performed. If geometry was not checked, say so explicitly. If a chamfer or
fillet was disabled to work around an error, report the shape change and the
remaining limitation.

## References

- [JSON contract](https://github.com/gavroman/zoo-kcl-check/blob/main/docs/development.md#result-contract).
- [Zoo CLI: installation, authentication, and commands](https://zoo.dev/docs/developer-tools/cli/manual).
- [Modeling rules](https://github.com/gavroman/zoo-kcl-check/blob/main/skills/kcl-modeling/SKILL.md).
