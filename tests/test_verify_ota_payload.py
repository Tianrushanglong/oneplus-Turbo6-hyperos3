from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verifier = load_module(
    "verify_ota_payload", ROOT / "tools" / "verify_ota_payload.py"
)


class OtaPayloadVerifierTests(unittest.TestCase):
    def make_ota(self, path: Path, corrupt_hash: bool = False) -> None:
        payload = b"CrAU" + bytes(range(256)) * 32
        metadata_size = 128
        file_hash = hashlib.sha256(payload).digest()
        if corrupt_hash:
            file_hash = b"\0" * 32
        properties = {
            "FILE_HASH": base64.b64encode(file_hash).decode("ascii"),
            "FILE_SIZE": str(len(payload)),
            "METADATA_HASH": base64.b64encode(
                hashlib.sha256(payload[:metadata_size]).digest()
            ).decode("ascii"),
            "METADATA_SIZE": str(metadata_size),
        }
        metadata = (
            "ota-type=AB\n"
            "product_name=PLU110\n"
            "version_name=PLU110_16.0.2.408(CN01)\n"
            "post-sdk-level=36\n"
        )
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("META-INF/com/android/metadata", metadata)
            archive.writestr(
                "payload_properties.txt",
                "".join(f"{key}={value}\n" for key, value in properties.items()),
            )
            archive.writestr("payload.bin", payload)

    def test_verifies_payload_and_metadata_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ota = Path(directory) / "base.zip"
            self.make_ota(ota)
            report = verifier.verify(
                ota,
                expected_product="PLU110",
                expected_version="PLU110_16.0.2.408(CN01)",
            )
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(report.payload_magic, "CrAU")
            self.assertEqual(report.post_sdk_level, "36")

    def test_rejects_wrong_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ota = Path(directory) / "base.zip"
            self.make_ota(ota, corrupt_hash=True)
            report = verifier.verify(ota)
            self.assertTrue(any("FILE_HASH" in error for error in report.errors))

    def test_rejects_wrong_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ota = Path(directory) / "base.zip"
            self.make_ota(ota)
            report = verifier.verify(
                ota, expected_product="OTHER", expected_version="wrong"
            )
            self.assertTrue(any("product_name" in error for error in report.errors))
            self.assertTrue(any("version_name" in error for error in report.errors))


if __name__ == "__main__":
    unittest.main()
