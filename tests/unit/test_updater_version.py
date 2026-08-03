from __future__ import annotations

import unittest
import urllib.error

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from suite_launcher import updater


class VersionParsingTests(unittest.TestCase):
    def test_parse_version_strips_leading_v(self) -> None:
        self.assertEqual(updater.parse_version("v1.4.0"), (1, 4, 0))
        self.assertEqual(updater.parse_version("1.4.0"), (1, 4, 0))

    def test_parse_version_handles_missing_or_non_numeric_parts(self) -> None:
        self.assertEqual(updater.parse_version("v2"), (2,))
        self.assertEqual(updater.parse_version(""), (0,))
        self.assertEqual(updater.parse_version("vbeta"), (0,))

    def test_is_newer_compares_numerically_not_lexically(self) -> None:
        self.assertTrue(updater.is_newer("0.9.0", "0.10.0"))
        self.assertFalse(updater.is_newer("1.4.0", "1.4.0"))
        self.assertFalse(updater.is_newer("1.4.1", "1.4.0"))


class ReleasePayloadParsingTests(unittest.TestCase):
    def test_parse_release_payload_picks_zip_asset(self) -> None:
        payload = {
            "tag_name": "v1.5.0",
            "body": "Adds auto-update.",
            "assets": [
                {"name": "checksums.txt", "browser_download_url": "https://example.invalid/checksums.txt"},
                {"name": "LSPR-Suite-Portable-v1.5.0.zip", "browser_download_url": "https://example.invalid/bundle.zip"},
            ],
        }
        info = updater._parse_release_payload(payload)
        self.assertEqual(info.tag, "v1.5.0")
        self.assertEqual(info.version, (1, 5, 0))
        self.assertEqual(info.notes, "Adds auto-update.")
        self.assertEqual(info.download_url, "https://example.invalid/bundle.zip")
        self.assertEqual(info.asset_name, "LSPR-Suite-Portable-v1.5.0.zip")

    def test_parse_release_payload_requires_a_zip_asset(self) -> None:
        payload = {"tag_name": "v1.5.0", "assets": [{"name": "notes.txt", "browser_download_url": "x"}]}
        with self.assertRaises(LookupError):
            updater._parse_release_payload(payload)

    def test_parse_release_payload_requires_a_tag(self) -> None:
        payload = {"tag_name": "", "assets": [{"name": "bundle.zip", "browser_download_url": "x"}]}
        with self.assertRaises(LookupError):
            updater._parse_release_payload(payload)


class FetchLatestReleaseTests(unittest.TestCase):
    def test_fetch_latest_release_surfaces_network_errors(self) -> None:
        def _raise_urlopen(*_args, **_kwargs):
            raise urllib.error.URLError("no internet")

        original_urlopen = updater.urllib.request.urlopen
        updater.urllib.request.urlopen = _raise_urlopen
        try:
            with self.assertRaises(urllib.error.URLError):
                updater.fetch_latest_release()
        finally:
            updater.urllib.request.urlopen = original_urlopen


class AppReleasePayloadParsingTests(unittest.TestCase):
    def test_parse_app_release_payload_needs_no_asset(self) -> None:
        # Unlike the Suite bundle, per-app releases have no downloadable zip -
        # they exist only so the launcher can compare tagged versions.
        payload = {
            "tag_name": "v0.5.0",
            "body": "Adds pump calibration.",
            "published_at": "2026-07-01T12:00:00Z",
        }
        info = updater._parse_app_release_payload("lednicky-t/SingleSpotLSPR-Acquisition", payload)
        self.assertEqual(info.repo, "lednicky-t/SingleSpotLSPR-Acquisition")
        self.assertEqual(info.tag, "v0.5.0")
        self.assertEqual(info.version, (0, 5, 0))
        self.assertEqual(info.notes, "Adds pump calibration.")
        self.assertEqual(info.published_at, "2026-07-01T12:00:00Z")

    def test_parse_app_release_payload_requires_a_tag(self) -> None:
        with self.assertRaises(LookupError):
            updater._parse_app_release_payload("owner/repo", {"tag_name": ""})


class FetchLatestAppReleaseTests(unittest.TestCase):
    def test_fetch_latest_app_release_surfaces_network_errors(self) -> None:
        def _raise_urlopen(*_args, **_kwargs):
            raise urllib.error.URLError("no internet")

        original_urlopen = updater.urllib.request.urlopen
        updater.urllib.request.urlopen = _raise_urlopen
        try:
            with self.assertRaises(urllib.error.URLError):
                updater.fetch_latest_app_release("owner/repo")
        finally:
            updater.urllib.request.urlopen = original_urlopen


class FormatReleaseDateTests(unittest.TestCase):
    def test_format_release_date_strips_time_component(self) -> None:
        self.assertEqual(updater.format_release_date("2026-07-01T12:00:00Z"), "2026-07-01")

    def test_format_release_date_handles_empty_string(self) -> None:
        self.assertEqual(updater.format_release_date(""), "")


if __name__ == "__main__":
    unittest.main()
