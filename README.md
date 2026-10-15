# zoo-kcl-check-cli

Local checks for [Zoo / KittyCAD KCL](https://zoo.dev/): formatting, syntax,
style, execution errors, and sketch constraints.
Works offline, without a Zoo account or API tokens.

Execution uses a mock CAD engine. **A successful check does not validate
3D geometry** — verify it in Zoo.

## Installation

```sh
pipx install zoo-kcl-check-cli
```

Also available via `pip install zoo-kcl-check-cli` or `uv tool install zoo-kcl-check-cli`.
Requires Python 3.11–3.14. [Package on PyPI](https://pypi.org/project/zoo-kcl-check-cli/).

## Usage

```sh
kcl-check lint main.kcl
kcl-check execute main.kcl
kcl-check sketches main.kcl
kcl-check format main.kcl --write
```

| Command | Purpose |
|---|---|
| `lint` | Check syntax and style without execution |
| `execute` | Local execution: names, arguments, types, units, and `assert` |
| `sketches` | Check whether sketch constraints fully define sketches and whether they conflict |
| `format` | Print formatted code by default, write with `--write`, check with `--check` |

You can pass a directory instead of a file:

```sh
kcl-check lint .
kcl-check execute .
kcl-check sketches .
kcl-check format . --check
```

`lint` and `format` process `.kcl` files recursively, skipping symbolic links.
`execute` and `sketches` run `default_file` from `project.toml` or `main.kcl`.
Imports of neighboring KCL files are supported.

Warnings also cause failure. The `sketches` command fails for under-constrained
or conflicting sketches and incomplete reports.

Use `kcl-check COMMAND --help` for command options.

## Output

Output is text by default. Diagnostics go to stderr; results and reports go to
stdout. Engine errors include a code excerpt and a pointer to the problem
when that information is available.

For scripts, CI, and coding agents:

```sh
kcl-check execute main.kcl --json
```

`--json` returns one object in stdout, including on failure.
Main fields: `ok`, `exit_code`, `diagnostics`, `sketches`, `formatting`.
See the [result contract](docs/development.md#result-contract) for the full format.

All commands support `--timeout SECONDS`; the default is 60 seconds for the entire command.

| Exit code | Meaning |
|---|---|
| `0` | Check passed |
| `1` | KCL diagnostics, constraint problems, or formatting differences with `--check` |
| `2` | Invalid input, timeout, or tool failure |
| `130` | Interrupted by the user |

## Agent skills

- [kcl-modeling](https://github.com/gavroman/zoo-kcl-check/blob/main/skills/kcl-modeling/SKILL.md) — modeling rules and KCL techniques.
- [kcl-check](https://github.com/gavroman/zoo-kcl-check/blob/main/skills/kcl-check/SKILL.md) — CLI installation, running checks, and interpreting results.

```sh
npx skills add gavroman/zoo-kcl-check --skill kcl-modeling --skill kcl-check
```

## Development

[Setup, tests, and builds](docs/development.md).
Report bugs and suggestions in [GitHub Issues](https://github.com/gavroman/zoo-kcl-check/issues).

## License

[MIT](LICENSE).
