"""Collect the licence texts of every installed third-party package into one folder, for the portable bundle.

The portable bundle redistributes everything that is installed in its Python (PyQt6, numpy, ...), and almost every one of those licences
requires its notice to travel with the copy; the GPL of PyQt6 also asks for its text. `build_portable.ps1` runs this with the bundle's own
Python after the last `pip install`:

    python collect_third_party_licenses.py <destination-folder>

It writes one folder per package (`<name>-<version>/`, holding the licence files that package installed) and an `INDEX.txt` that lists every
package with its declared licence and how many files were found. A package for which no file was found (and none is kept in
`extra_licenses/`) is listed as such: that one needs a manual look. The Suite's own packages (`lspr*`) are skipped: the repository's LICENSE covers them.

Standard library only, so it runs in a bare bundle.
"""

from __future__ import annotations

import re
import shutil
import sys
from importlib import metadata
from pathlib import Path

_OWN_PREFIXES = ("lspr",)
# For a package that ships no licence file although its licence asks for the notice (see extra_licenses/README.md): `<name in lower case>.txt`.
EXTRA_DIR = Path(__file__).resolve().parent / "extra_licenses"
# A licence file by name: LICENSE, LICENCE, COPYING, NOTICE, also with a suffix (LICENSE.txt, LICENSE-APACHE, NOTICE.md) but never a source file.
_LICENCE_FILE = re.compile(r"^(licen[cs]e|copying|notice)([-_.].*)?$", re.IGNORECASE)
_SOURCE_SUFFIXES = (".py", ".pyi", ".pyc", ".pyd", ".so", ".dll")
# Some packages keep their texts as `licenses/MIT.txt` or `LICENSES/BSD-3-Clause.txt`: the folder says what the file is.
_LICENCE_FOLDERS = {"licenses", "licences", "license", "licence"}


def _is_licence_file(path: Path) -> bool:
    if path.suffix.lower() in _SOURCE_SUFFIXES:
        return False
    return bool(_LICENCE_FILE.match(path.name)) or any(part.lower() in _LICENCE_FOLDERS for part in path.parts[:-1])


def _licence_label(distribution: metadata.Distribution) -> str:
    """The licence the package declares: its SPDX expression, else the first line of its License field, else its classifiers."""
    info = distribution.metadata
    expression = (info.get("License-Expression") or "").strip()
    if expression:
        return expression
    field = (info.get("License") or "").strip().splitlines()
    if field and len(field[0]) <= 80:
        return field[0]
    classifiers = [c.split("::")[-1].strip() for c in (info.get_all("Classifier") or []) if c.startswith("License ::")]
    return "; ".join(classifiers) or "(not declared)"


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]+", "_", text)


def collect(destination: Path, extra_dir: Path = EXTRA_DIR) -> list[tuple[str, str, str, int]]:
    """Copy the licence files of every installed package into *destination*; return ``(name, version, licence, files copied)`` rows.
    A package that brought no file gets the one from *extra_dir*, if there is one for it."""
    destination.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for distribution in sorted(metadata.distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
        name = distribution.metadata.get("Name") or ""
        if not name or name.lower().startswith(_OWN_PREFIXES):
            continue
        version = distribution.version or "unknown"
        if (name.lower(), version) in seen:  # the same package found twice on the path
            continue
        seen.add((name.lower(), version))
        folder = destination / f"{_safe(name)}-{_safe(version)}"
        copied = 0
        for entry in distribution.files or []:
            relative = Path(str(entry))
            if not _is_licence_file(relative):
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file():
                continue
            folder.mkdir(parents=True, exist_ok=True)
            # Flatten the path inside the package (licenses/src/pip/_vendor/x/LICENSE.txt) into one unambiguous file name.
            parts = [p for p in relative.parts if not p.endswith(".dist-info")]
            shutil.copyfile(source, folder / _safe("__".join(parts)))
            copied += 1
        fallback = extra_dir / f"{name.lower()}.txt"
        if copied == 0 and fallback.is_file():
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fallback, folder / f"extra__{fallback.name}")
            copied += 1
        rows.append((name, version, _licence_label(distribution), copied))
    index = ["Licence texts of the third-party packages installed in this bundle.", "", f"{'package':32s} {'version':14s} {'files':>5s}  licence"]
    for name, version, label, copied in rows:
        index.append(f"{name:32s} {version:14s} {copied:5d}  {label}" + ("   <-- NO LICENCE FILE FOUND, check by hand" if copied == 0 else ""))
    (destination / "INDEX.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    return rows


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python collect_third_party_licenses.py <destination-folder>", file=sys.stderr)
        return 2
    rows = collect(Path(argv[1]))
    missing = [name for name, _version, _label, copied in rows if copied == 0]
    print(f"Collected licence texts of {len(rows)} packages into {argv[1]}.")
    if missing:
        print(f"WARNING: no licence file found for {len(missing)} package(s): {', '.join(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
