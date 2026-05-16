# Versioning

LSPR Acquisition uses semantic versioning:

```text
MAJOR.MINOR.PATCH
```

- `MAJOR` changes for incompatible project or file-format changes.
- `MINOR` changes for new features that remain backward compatible.
- `PATCH` changes for bug fixes and small maintenance releases.

## Single Version Source

The application version is defined in:

```text
src/lspr_app/_version.py
```

`pyproject.toml` reads this value through setuptools dynamic metadata, so package builds and the running app share the same version.

## Release Checklist

1. Update `src/lspr_app/_version.py`.
2. Add a matching entry to `CHANGELOG.md`.
3. Commit the release changes.
4. Create an annotated git tag:

```powershell
git tag -a v0.1.0 -m "LSPR Acquisition 0.1.0"
```

5. Push the commit and tag:

```powershell
git push
git push origin v0.1.0
```

## Git Tags

Git release tags use the `vX.Y.Z` form, for example:

```text
v0.1.0
```

Tags should point to committed release states. Avoid tagging a dirty working tree because the tag will not include uncommitted files.
