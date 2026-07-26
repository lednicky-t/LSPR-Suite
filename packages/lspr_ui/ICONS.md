# Icons in the LSPR Suite

All four apps get their icons from one place: `packages/lspr_ui/src/lspr_ui/icon_assets/`,
a folder of individually vendored SVG files, loaded through `lspr_ui.load_tabler_icon()`
(or the `tabler_icon()` / `tint_tabler_icon()` wrappers built on top of it).

**There is no icon-library dependency in this project, and there should not be one again.**
Read the "Why" section below before adding a new icon-library package to any `pyproject.toml`
or `requirements.txt`.

---

## Why individually vendored SVGs, not a library dependency

Until 2026-07, `apps/sLSPR/acq` and `packages/lspr_ui` depended on the `tabler-qicon` PyPI
package, and `apps/LSPRi/eva` separately depended on `tabler-icons` (a different PyPI package,
same underlying [Tabler Icons](https://tabler.io/icons) artwork, different packaging). Both are
"whole icon library" packages: thousands of SVGs, of which this project used well under 200
combined.

That had a real, measured cost: profiling `singleLSPR Acquisition`'s startup showed the first
call into the icon library (indexing/scanning its bundled icon directory or zip archive) cost
roughly **1 second** of a startup budget where every hundred milliseconds is noticeable to a
scientist waiting to start a measurement. The fix wasn't "compile the library" (Python doesn't
compile away unused files) - it was to stop shipping thousands of icons this suite doesn't use
and only load the ~100 it does, from local files, with an in-process cache
(`functools.lru_cache`) so repeat lookups are instant.

The two apps also drifted onto two different packages for the same artwork, which meant real
duplicated logic (two separate SVG-recoloring code paths, two separate optional-import guards)
for no benefit.

So: one shared, curated set of SVGs, one loader, in `lspr_ui`, used by every app.

---

## How it works

- `packages/lspr_ui/src/lspr_ui/icon_assets/<name>.svg` - one file per vendored icon. Filenames
  use Tabler's native hyphenated naming (e.g. `player-stop.svg`, `chevron-left.svg`).
- `lspr_ui.load_tabler_icon(name, *, color=None, size=24, stroke_width=None, fill=None) -> QIcon`
  reads the file (name accepts hyphens or underscores), substitutes the `currentColor` /
  `fill="none"` placeholders for the requested color, renders it via `QSvgRenderer`, and returns
  a `QIcon`. Results per raw SVG template are cached (`functools.lru_cache`); returns an empty
  `QIcon()` if the name isn't vendored.
- `lspr_ui.tabler_icon(*names) -> QIcon` tries each name in order and returns the first one that
  resolves - useful for a preferred-name-with-fallback pattern.
- `lspr_ui.tabler_icon_svg(name, *, color=None, stroke_width=None, fill=None) -> str | None`
  returns the recolored SVG markup itself (not a `QIcon`) for the rare call site that needs to
  render into a custom-cropped `QRectF` instead of a plain square icon.
- `lspr_ui.available_tabler_icon_names() -> list[str]` lists every vendored name - useful for
  tests/tooling, and for checking whether a name is already vendored before adding it again.

Every app's own icon-wrapper module (`lspr_app/gui/icon_helpers.py` for singleLSPR Acquisition,
`MainWindowIcons._tabler_icon()` for LSPRimaging Evaluation) is a thin call-through to these
functions. Don't call `load_tabler_icon()` directly from app GUI code that already has a local
wrapper - use the wrapper, so there's still one place per app that owns icon call sites.

---

## Adding a new icon

1. Check `lspr_ui.available_tabler_icon_names()` (or just look in `icon_assets/`) - it might
   already be vendored under a slightly different name.
2. Get the SVG from [tabler.io/icons](https://tabler.io/icons) (MIT licensed). Use the **outline**
   variant unless you specifically need a filled icon.
3. Save it as `packages/lspr_ui/src/lspr_ui/icon_assets/<hyphenated-name>.svg`, matching Tabler's
   own naming for that icon exactly - keep this queryable/predictable, don't invent a name.
4. Make sure the SVG uses `stroke="currentColor"` (outline) or `fill="currentColor"` (filled) -
   that's the placeholder `load_tabler_icon()` substitutes with the requested color at render
   time. Tabler's icons already use this convention; if you pasted from somewhere else, check.
5. Strip anything decorative and unnecessary before saving: outer HTML comments, `class`
   attributes, and Tabler's fully-transparent `<path stroke="none" d="M0 0h24v24H0z" fill="none"/>`
   click-target padding rect (meaningless for a `QIcon`, just extra bytes).
6. Call it via the app's own icon-wrapper module, not `lspr_ui.load_tabler_icon()` directly from
   GUI code (see "How it works" above).

No new PyPI dependency is needed for this - it's one static file plus a function call that
already exists.

---

## For AI agents (and anyone reviewing their changes)

**Do not add a new icon-library dependency** (another `pip install some-icon-pack`) to fill an
icon need, even a small one, and even if it seems like the "correct"/idiomatic way to get an
icon in a Qt app. Vendor the individual SVG file instead, following the steps above. This is a
deliberate, previously-litigated decision (see "Why" above), not an oversight - if a library
dependency looks tempting because you need many icons at once, that's a signal to pause and ask
the maintainer first rather than reintroduce the problem this migration fixed.

If you're not sure whether an icon is "worth" vendoring individually versus reaching for a
library, the answer is almost always vendor it - the cost is one small file and 30 seconds of
work; a library dependency's cost only shows up later, at startup, spread across users.
