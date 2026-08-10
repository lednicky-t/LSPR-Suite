from __future__ import annotations

import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory

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


class _FakeTruncatedResponse:
    """Mimics an HTTPResponse whose connection closes early: declares a
    Content-Length but only ever yields fewer bytes than that, the way a
    proxy/antivirus scanner that drops a large download mid-stream would."""

    def __init__(self, full_body: bytes, declared_length: int) -> None:
        self._remaining = full_body
        self.length = None
        self.headers = {"Content-Length": str(declared_length)}

    def __enter__(self) -> "_FakeTruncatedResponse":
        return self

    def __exit__(self, *_exc_info) -> None:
        return None

    def read(self, size: int) -> bytes:
        chunk, self._remaining = self._remaining[:size], self._remaining[size:]
        return chunk


class DownloadReleaseTests(unittest.TestCase):
    def test_download_release_raises_on_truncated_transfer(self) -> None:
        info = updater.ReleaseInfo(
            tag="v1.5.0",
            version=(1, 5, 0),
            notes="",
            download_url="https://example.invalid/bundle.zip",
            asset_name="bundle.zip",
        )
        # Server claims 100 bytes are coming but the connection only delivers 10.
        fake_response = _FakeTruncatedResponse(b"0123456789", declared_length=100)

        original_urlopen = updater.urllib.request.urlopen
        updater.urllib.request.urlopen = lambda *_a, **_kw: fake_response
        try:
            with TemporaryDirectory() as tmp_dir:
                dest_path = Path(tmp_dir) / "bundle.zip"
                with self.assertRaises(OSError):
                    updater.download_release(info, dest_path)
                # The partial file must not be left behind for the updater to
                # trip over later as a confusing "could not unpack" error.
                self.assertFalse(dest_path.exists())
        finally:
            updater.urllib.request.urlopen = original_urlopen


class FormatReleaseDateTests(unittest.TestCase):
    def test_format_release_date_strips_time_component(self) -> None:
        self.assertEqual(updater.format_release_date("2026-07-01T12:00:00Z"), "2026-07-01")

    def test_format_release_date_handles_empty_string(self) -> None:
        self.assertEqual(updater.format_release_date(""), "")


if __name__ == "__main__":
    unittest.main()
