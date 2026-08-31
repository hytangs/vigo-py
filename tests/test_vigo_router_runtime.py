"""Tests for checksum-verified standalone runtime installation."""

import hashlib
import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import vigo_router.runtime as runtime_module
from vigo_router.runtime import install_runtime


class VigoRuntimeInstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "VIGO-0.2.3-mac-arm64.zip"
        launcher = (
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo 0.2.3; fi\n'
            'if [ "$1" = "--help" ]; then echo \'vigo build-network; '
            "vigo route-ndjson; vigo one-to-many; vigo isochrone'; fi\n"
        )
        with zipfile.ZipFile(self.archive, "w") as bundle:
            info = zipfile.ZipInfo("VIGO-mac-arm64/vigo")
            info.external_attr = 0o100755 << 16
            bundle.writestr(info, launcher)
            bundle.writestr(
                "VIGO-mac-arm64/VIGO.app/Contents/Resources/bin/vigo.mjs",
                "// fixture\n",
            )
        self.sha256 = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def tearDown(self):
        self.temporary.cleanup()

    def test_installs_verified_runtime_and_reuses_it(self):
        destination = self.root / "runtime"
        installed = install_runtime(
            archive=self.archive,
            sha256=self.sha256,
            destination=destination,
            version="0.2.3",
        )
        self.assertEqual(installed.version, "0.2.3")
        self.assertTrue(installed.launcher.is_file())
        self.assertTrue(installed.launcher.stat().st_mode & 0o100)
        self.assertFalse(installed.reused)

        reused = install_runtime(
            archive=self.root / "missing.zip",
            sha256=self.sha256,
            destination=destination,
            version="0.2.3",
        )
        self.assertTrue(reused.reused)
        self.assertEqual(reused.launcher, installed.launcher)

    def test_rejects_checksum_mismatch_and_unsafe_member(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            install_runtime(
                archive=self.archive,
                sha256="0" * 64,
                destination=self.root / "bad-checksum",
                version="0.2.3",
            )

        unsafe = self.root / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w") as bundle:
            bundle.writestr("../escape", "unsafe")
        unsafe_sha = hashlib.sha256(unsafe.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            install_runtime(
                archive=unsafe,
                sha256=unsafe_sha,
                destination=self.root / "unsafe-runtime",
                version="0.2.3",
            )

    def test_rejects_non_https_runtime_url(self):
        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            install_runtime(
                url="http://example.test/VIGO.zip",
                sha256=self.sha256,
                destination=self.root / "downloaded",
                version="0.2.3",
            )

    def test_download_allows_https_and_rejects_insecure_redirect(self):
        class Response(io.BytesIO):
            def __init__(self, contents: bytes, url: str):
                super().__init__(contents)
                self.url = url

            def geturl(self):
                return self.url

        downloaded = self.root / "downloaded.zip"
        with patch.object(
            runtime_module.urllib.request,
            "urlopen",
            return_value=Response(b"archive", "https://cdn.example.test/VIGO.zip"),
        ):
            runtime_module._download(
                "https://example.test/VIGO.zip",
                downloaded,
            )
        self.assertEqual(downloaded.read_bytes(), b"archive")

        destination = self.root / "redirected.zip"
        with (
            patch.object(
                runtime_module.urllib.request,
                "urlopen",
                return_value=Response(
                    b"archive",
                    "http://example.test/VIGO.zip",
                ),
            ),
            self.assertRaisesRegex(ValueError, "redirect must use HTTPS"),
        ):
            runtime_module._download(
                "https://example.test/VIGO.zip",
                destination,
            )

        self.assertFalse(destination.exists())

    def test_rejects_same_version_runtime_without_analysis_commands(self):
        legacy = self.root / "legacy.zip"
        launcher = (
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo 0.2.3; fi\n'
            'if [ "$1" = "--help" ]; then echo \'vigo build-network\'; fi\n'
        )
        with zipfile.ZipFile(legacy, "w") as bundle:
            info = zipfile.ZipInfo("VIGO-mac-arm64/vigo")
            info.external_attr = 0o100755 << 16
            bundle.writestr(info, launcher)
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError, "one-to-many"):
            install_runtime(
                archive=legacy,
                sha256=digest,
                destination=self.root / "legacy-runtime",
                version="0.2.3",
            )

    def test_refuses_to_replace_non_vigo_destination(self):
        destination = self.root / "occupied"
        destination.mkdir()
        marker = destination / "keep.txt"
        marker.write_text("user data\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "non-VIGO runtime destination"):
            install_runtime(
                archive=self.archive,
                sha256=self.sha256,
                destination=destination,
                version="0.2.3",
                force=True,
            )

        self.assertEqual(marker.read_text(encoding="utf-8"), "user data\n")

    def test_can_install_into_existing_empty_destination(self):
        destination = self.root / "empty"
        destination.mkdir()

        installed = install_runtime(
            archive=self.archive,
            sha256=self.sha256,
            destination=destination,
            version="0.2.3",
        )

        self.assertEqual(installed.root, destination.resolve())
        self.assertTrue(installed.launcher.is_file())

    def test_source_checkout_finds_repository_release_archive(self):
        module_path = self.root / "repo" / "vigo_router" / "runtime.py"
        archive = self.root / "repo" / "release" / "VIGO-0.2.3-mac-arm64.zip"
        module_path.parent.mkdir(parents=True)
        archive.parent.mkdir(parents=True)
        module_path.write_text("# fixture\n", encoding="utf-8")
        archive.write_bytes(b"fixture")

        with patch.object(runtime_module, "__file__", str(module_path)):
            self.assertEqual(
                runtime_module._local_release_archive("0.2.3"),
                archive.resolve(),
            )

    def test_no_argument_install_uses_pinned_official_release(self):
        destination = self.root / "official-runtime"
        release_url = "https://example.test/VIGO-0.2.3-mac-arm64.zip"

        def copy_archive(_url, output):
            shutil.copyfile(self.archive, output)

        with (
            patch.object(runtime_module, "_local_release_archive", return_value=None),
            patch.object(
                runtime_module,
                "_OFFICIAL_RUNTIME_RELEASES",
                {"0.2.3": {"url": release_url, "sha256": self.sha256}},
            ),
            patch.object(runtime_module, "_require_official_runtime_platform"),
            patch.object(runtime_module, "_download", side_effect=copy_archive),
        ):
            installed = install_runtime(
                destination=destination,
                version="0.2.3",
            )

        self.assertEqual(installed.source, release_url)
        self.assertEqual(installed.archive_sha256, self.sha256)
        self.assertTrue(installed.launcher.is_file())

    def test_official_runtime_rejects_unsupported_platform(self):
        with (
            patch.object(runtime_module, "_local_release_archive", return_value=None),
            patch.object(runtime_module.platform, "system", return_value="Linux"),
            patch.object(runtime_module.platform, "machine", return_value="x86_64"),
            self.assertRaisesRegex(RuntimeError, "Apple-silicon macOS"),
        ):
            install_runtime(
                destination=self.root / "unsupported",
                version="0.3.0",
            )


if __name__ == "__main__":
    unittest.main()
