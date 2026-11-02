"""`lspr_core.citation`: how to cite LSPR Suite, and the stamp the apps write into their files.

The constants must stay equal to the repository's `CITATION.cff` (what GitHub's "Cite this repository" button and Zenodo show), and the
wording of the disclaimer must stay in `NOTICE.md`; a change to one without the other fails here instead of drifting."""

from __future__ import annotations

import re
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

from lspr_core import (  # noqa: E402
    ACKNOWLEDGEMENT_REQUEST,
    DISCLAIMER_POINTS,
    LICENSE_NAME,
    NOTICE_URL,
    SUITE_DOI,
    SUITE_NAME,
    SUITE_URL,
    citation_text,
    doi_url,
    software_stamp,
)

CITATION_CFF = REPO_ROOT / "CITATION.cff"
NOTICE_MD = REPO_ROOT / "NOTICE.md"


def _cff_field(text: str, key: str) -> str:
    match = re.search(rf'^\s*(?:-\s+)?{re.escape(key)}:\s*"?([^"\r\n]+)"?\s*$', text, re.MULTILINE)
    assert match, f"{key} not found in CITATION.cff"
    return match.group(1).strip()


@unittest.skipUnless(CITATION_CFF.is_file(), "CITATION.cff is not in this checkout")
class CitationMatchesTheRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cff = CITATION_CFF.read_text(encoding="utf-8")

    def test_the_constants_equal_the_citation_file(self) -> None:
        self.assertEqual(_cff_field(self.cff, "title"), SUITE_NAME)
        self.assertEqual(_cff_field(self.cff, "repository-code"), SUITE_URL)
        self.assertEqual(_cff_field(self.cff, "license"), "MIT")
        self.assertIn(f"value: \"{SUITE_DOI}\"", self.cff)
        self.assertTrue(LICENSE_NAME.startswith("MIT"))

    def test_the_author_is_the_one_of_the_citation_file(self) -> None:
        self.assertEqual(_cff_field(self.cff, "family-names"), "Lednický")
        self.assertEqual(_cff_field(self.cff, "given-names"), "Tomáš")
        self.assertTrue(citation_text().startswith("Lednický, T. "))

    def test_the_version_in_the_citation_file_is_a_real_version(self) -> None:
        # The auto-release workflow keeps it on the released version; it only has to look like one.
        self.assertRegex(_cff_field(self.cff, "version"), r"^\d+\.\d+\.\d+$")
        self.assertRegex(_cff_field(self.cff, "date-released"), r"^\d{4}-\d{2}-\d{2}$")


class CitationTextTests(unittest.TestCase):
    def test_the_line_names_the_suite_the_app_the_version_and_the_doi(self) -> None:
        self.assertEqual(citation_text(), f"Lednický, T. LSPR Suite. Zenodo. https://doi.org/{SUITE_DOI}")
        line = citation_text("LSPRimaging Evaluation", "0.3.0")
        self.assertEqual(line, f"Lednický, T. LSPR Suite - LSPRimaging Evaluation, version 0.3.0. Zenodo. https://doi.org/{SUITE_DOI}")
        self.assertEqual(doi_url(), f"https://doi.org/{SUITE_DOI}")
        self.assertEqual(NOTICE_URL, f"{SUITE_URL}/blob/main/NOTICE.md")

    def test_the_stamp_is_what_a_file_needs_to_carry(self) -> None:
        stamp = software_stamp("LSPR Acquisition", "0.4.1")
        self.assertEqual(set(stamp), {"name", "version", "doi", "citation"})
        self.assertEqual(stamp["name"], "LSPR Suite - LSPR Acquisition")
        self.assertEqual((stamp["version"], stamp["doi"]), ("0.4.1", SUITE_DOI))
        self.assertEqual(stamp["citation"], citation_text("LSPR Acquisition", "0.4.1"))
        self.assertTrue(all(isinstance(value, str) for value in stamp.values()))  # HDF5 attributes and Zarr JSON take plain strings


@unittest.skipUnless(NOTICE_MD.is_file(), "NOTICE.md is not in this checkout")
class NoticeSaysTheSameTests(unittest.TestCase):
    def test_notice_md_carries_the_points_the_apps_show(self) -> None:
        notice = " ".join(NOTICE_MD.read_text(encoding="utf-8").split()).lower()
        self.assertIn("as is", notice)
        self.assertIn("diagnostic", notice)
        self.assertIn("backups", notice)
        self.assertIn("acknowledg", notice)
        self.assertEqual(len(DISCLAIMER_POINTS), 3)
        self.assertIn("cite", ACKNOWLEDGEMENT_REQUEST)


if __name__ == "__main__":
    unittest.main()
