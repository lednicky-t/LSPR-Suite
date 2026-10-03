# Code-health tools (advisory)

Moved from the root `CLAUDE.md` so that it is not loaded in every session. The root file keeps the one rule that matters: agents never run these tools on their own initiative.

## The tools

Five dev-only tools are installed by `requirements-dev.txt`. They are **not** wired into pre-commit or CI. Running them is manual, by design.

- `radon`: complexity. Use `radon mi` to see whether a file's maintainability actually improved.
- `pytest-cov`: test coverage.
- `vulture`: dead code.
- `import-linter`: layering rules. Reads `.importlinter`.
- `mypy`: type checks.

Use each tool's standard command from `requirements-dev.txt`.

- Run `mypy` **per package or app root**. A combined multi-root run fails with "Duplicate module named ...", because each app's `src/` has its own top-level `main.py`.
- `lspr_core` and `lspr_io` are kept free of GUI imports and mypy-clean.

## When to suggest each tool

Name the tool and the reason, then wait for the maintainer to say go ahead.

- After a refactor that touches many files in one app or package: `import-linter`, to confirm no layering rule broke.
- When a GUI file keeps growing, or after splitting one further: `radon mi`.
- Before a release, or after finishing a feature branch: `pytest-cov`.
- After deleting or renaming code (an old alias, a legacy fallback path): `vulture`, to check nothing was left behind.
- After touching a dataclass or attribute contract shared across mixins or controllers: `mypy` on that file, to catch stale type annotations such as a field typed as the base Qt class instead of the custom subclass assigned to it.
- When the maintainer asks directly about code quality, tech debt, or maintainability.

## Reading mypy output

Most mypy output comes from two structural false-positive patterns, not real bugs:

- `attr-defined` from the mixin/controller-split architecture. mypy checks each mixin alone and cannot see attributes defined on sibling mixins.
- `union-attr` from PyQt6 stubs. They type things as `X | None` even where Qt guarantees a value is not null.

Real bugs are mixed in. One example is a dataclass field typed as the base Qt widget class instead of a custom subclass. Triage each item on its own. Do not apply a blanket fix or suppression.
