---
name: kcl-modeling
description: >-
  Create and modify parametric CAD models in Zoo / KittyCAD Language (KCL): function structure, units, sketches, constraints, boolean operations, and fillets. Use for CAD .kcl files; do not use for the kcl-lang configuration language or development of the CLI itself.
---

# Modeling in KCL

[KCL (KittyCAD Language)](https://zoo.dev/docs/kcl-lang) is Zoo's parametric CAD
modeling language. Files with the `.kcl` extension describe sketches, constraints,
and operations that construct three-dimensional bodies.

## References

- [KCL functions](https://zoo.dev/docs/kcl-lang/functions).
- [Standard library](https://zoo.dev/docs/kcl-std).
- [KCL units](https://zoo.dev/docs/kcl-lang/numeric).
- [Fillets and face references](https://zoo.dev/docs/kcl-std/functions/std-solid-fillet).

Work within the current CAD project. Before making changes, read its entrypoint,
imports, and `project.toml`. Preserve the `@settings(kclVersion = ...)` version,
parameters, and the user's chosen model organization.

## Model structure

Use the following model structure unless the user explicitly chooses another:

- Use lowerCamelCase for function and variable names. Choose names that reflect
  the geometry, specify units for dimensions, and add short comments explaining
  the purpose of operations.
- Keep a separate source parameter block in the entrypoint after settings and
  imports, for example `sourceParameters`. Include only user-specified values,
  without derived dimensions or positions. Pass controlling dimensions to functions
  as named arguments; do not hide them inside construction code.
- Always declare `main(parameters)` in the entrypoint: it coordinates construction
  and placement and explicitly returns the completed bodies with `return`.
  Call it after the function declarations: `bodies = main(parameters = sourceParameters)`.
- Compute derived dimensions, clearances, distances, and positions in a separate
  `deriveParameters(parameters)` function that returns an object containing the
  results. Call it from `main`; do not put these calculations at the top level
  or directly in `main`.
- Separate profile creation, body construction, chamfers/fillets, and placement
  into functions. Do not repeat identical blocks for different size variants.
- Declare helper functions before the functions that call them: declaring them
  later can cause an `undefined value` error during KCL execution.
- Return each completed body from a function with an explicit `return`. Body
  construction function names must start with the exact prefix `makeBody`,
  for example `fn makeBodyPart(...) { ... return body }`.
- Keep source parameters, calculation and coordination functions, and the `main`
  call in the entrypoint. Create distinct parts and variants with separate calls
  to construction functions, including through `map` inside `main`.
- Validate dimensions with `assert`: positive diameters and height, outer diameter
  greater than inner diameter, valid thicknesses and radii, and non-intersecting
  chamfers. Preserve any existing option to disable a chamfer with `0mm`.

These are model organization conventions, not mandatory KCL syntax.
If sketches need to be edited with the mouse, warn the user about Zoo's limitation:
such editing is not supported inside user-defined functions. Preserve the user's
chosen function-based structure.

## Units and geometry references

- Specify numeric argument types: `length: mm`, `angle: deg`,
  `@index: number(_)`. Write dimensionless counts and coefficients as `2_`.
- Check the units of intermediate expressions. A type ascription such as
  `(x * cos(angle)): mm` does not fix incompatible units inside the expression.
- KCL 3.0 does not support area or volume types. Before squaring lengths, convert
  values to numbers in the same unit: `xValue = x / 1mm`, then
  `sqrt(xValue * xValue + yValue * yValue) * 1mm` to obtain a length again.
  Do not declare a squared length as type `mm` or use type ascription instead
  of conversion between different units.
- In `sideFaces` for `fillet` and `chamfer`, pass references to faces of the
  relevant body, not edges of the original sketch.
  For a body returned from a function, use, for example,
  `body.sketch.tags.leftSlope`; for an end face, use `body.faces.top`.
- Zoo engine limitation for `fillet`: a single operation cannot fillet more than
  two edges meeting at one vertex. Selecting a longitudinal edge and two end-face
  edges at its end causes `Unsupported edge selection`. Split the work into stages,
  respecting this limitation at each stage. Select edges on the result of the
  previous operation, accounting for new faces and topology changes.
- Equal radii in sequential operations can produce a degenerate transition.
  In a tested model, R2 on longitudinal corners combined with R2 on the ends
  caused `Fillet creation failed`; rounding two longitudinal corners to R4 first,
  followed by end-face edges to R2, succeeded with the real Zoo engine and was
  confirmed by the user after export. This is a specific workaround for that
  junction, not a general ban on equal radii or advice to always choose R4/R2.
  Splitting operations into stages alone does not guarantee successful construction:
  check the radii and surrounding geometry separately.
- Do not impose a general ban on fillets because of an internal `fillet` error.
  If a particular treatment is disabled as a workaround, explicitly report the
  shape change and remaining limitation; do not describe disabling the fillet
  as fixing the fillet itself.

## Sketch constraints

If a circle with a specified center and diameter still has a free start point,
check its direction: a fixed `0mm` instead of `var 0mm` for the Y coordinate
can remove an extra degree of freedom while preserving the parametric diameter.
Repeat the check and ensure that the new constraints do not conflict.

## Checks and reporting

After changing the model, use `kcl-check`: first format and write each modified
`.kcl` file, then run `lint`, `execute`, and `sketches` on the entire current
CAD project. Fix errors, warnings, lint findings, and under-constrained sketches.
After fixes, repeat formatting and all three checks until they succeed.
If the user explicitly requires keeping degrees of freedom, respect that choice,
but do not report a failed check as successful. If the backend is unavailable or
the tool fails repeatedly, report the blocker instead of retrying indefinitely.

Installation, commands, result formats, and Zoo CLI usage are covered by the
[kcl-check](https://github.com/gavroman/zoo-kcl-check/blob/main/skills/kcl-check/SKILL.md)
skill. If it is installed, read it before running checks. If it is unavailable,
use the [CLI README](https://github.com/gavroman/zoo-kcl-check) and `kcl-check COMMAND --help`.

`kcl-check` uses a mock engine: success does not confirm real B-Rep geometry
construction, boolean operations, or fillets. Full construction verification
from the command line requires Zoo CLI; Zoo Design Studio is an alternative.
Cloud checks may consume API credits: an ordinary request to change a model
is not, by itself, permission to run them. Run them at the user's explicit
request, without asking again for actions already authorized.

The absence of language server diagnostics does not prove validity. If the editor
shows a warning, inspect the expression even when local execution succeeds.
When working on a model, run the CLI on the model itself; tests of the `kcl-check`
implementation do not replace checks of the CAD project.

In the result, list the local checks and separately state whether real construction
was performed. If it was not checked, say so explicitly. Successful construction
or export does not prove compliance with dimensions, intended use, or manufacturing
requirements: verify the requested properties separately.
