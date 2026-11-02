"""How to cite LSPR Suite, in one place (standard library only).

The apps show this text in their About dialog, and write it into the files they create, so that the acknowledgement of the software
travels with the data. It is a request, not a condition of the licence (see NOTICE.md in the repository). The DOI is the Zenodo *concept*
DOI: it always resolves to the latest archived version. `tests/unit/test_lspr_core_citation.py` keeps these constants equal to the
repository's `CITATION.cff`.
"""

from __future__ import annotations

SUITE_NAME = "LSPR Suite"
SUITE_AUTHOR_FAMILY = "Lednický"
SUITE_AUTHOR_GIVEN = "Tomáš"
SUITE_DOI = "10.5281/zenodo.22111890"
SUITE_URL = "https://github.com/lednicky-t/LSPR-Suite"
NOTICE_URL = f"{SUITE_URL}/blob/main/NOTICE.md"
LICENSE_NAME = "MIT License"

# The wording shown in the apps (About dialog, the launcher's first-run notice); NOTICE.md says the same in full.
ACKNOWLEDGEMENT_REQUEST = (
    "If you use it to produce results in a publication, thesis or report, please cite it and name it in the acknowledgements."
)
DISCLAIMER_POINTS = (
    "Provided \"as is\", without warranty of any kind. The author is not liable for loss or corruption of data or for wrong results.",
    "Check the analysis on a measurement with a known answer, and keep your own backups of raw data.",
    "Not validated for diagnostic, clinical or safety-critical use.",
)


def doi_url() -> str:
    return f"https://doi.org/{SUITE_DOI}"


def citation_text(app_name: str | None = None, app_version: str | None = None) -> str:
    """One line to paste into a reference list: ``Lednický, T. LSPR Suite - <app>, version <x>. Zenodo. https://doi.org/...``."""
    label = SUITE_NAME
    if app_name:
        label += f" - {app_name}"
    if app_version:
        label += f", version {app_version}"
    return f"{SUITE_AUTHOR_FAMILY}, {SUITE_AUTHOR_GIVEN[0]}. {label}. Zenodo. {doi_url()}"


def software_stamp(app_name: str, app_version: str) -> dict[str, str]:
    """What an app writes into a file it creates: the software's name and version, its DOI and the citation line."""
    return {
        "name": f"{SUITE_NAME} - {app_name}",
        "version": str(app_version),
        "doi": SUITE_DOI,
        "citation": citation_text(app_name, app_version),
    }
