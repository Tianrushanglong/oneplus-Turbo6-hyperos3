from __future__ import annotations

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


rom_preflight = load_module("rom_preflight", ROOT / "tools" / "rom_preflight.py")
device_validator = load_module("device_validator", ROOT / "tools" / "validate_device_report.py")


class RomPreflightTests(unittest.TestCase):
    def make_ota(self, path: Path, metadata: str) -> None:
        payload = b"CrAU" + b"test payload"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META-INF/com/android/metadata", metadata)
            archive.writestr("payload_properties.txt", f"FILE_SIZE={len(payload)}\n")
            archive.writestr("payload.bin", payload)

    def test_valid_target_and_donor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "PLU110_16.0.2.408-full.zip"
            donor = root / "onyx-OS3-full.zip"
            self.make_ota(
                base,
                "post-build=OnePlus/PLU110/PLU110_16.0.2.408\npost-sdk-level=36\n",
            )
            self.make_ota(donor, "post-build=Xiaomi/onyx/OS3.0.1\npost-sdk-level=36\n")
            self.assertFalse(rom_preflight.inspect(base, "base").errors)
            self.assertFalse(rom_preflight.inspect(donor, "donor").errors)

    def test_rejects_wrong_donor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            donor = Path(directory) / "wrong.zip"
            self.make_ota(donor, "post-build=other/OS2\npost-sdk-level=35\n")
            errors = rom_preflight.inspect(donor, "donor").errors
            self.assertTrue(any("onyx" in error for error in errors))
            self.assertTrue(any("SDK" in error for error in errors))


class DeviceReportTests(unittest.TestCase):
    def test_accepts_expected_unlocked_device(self) -> None:
        values = {
            "ro.product.model": "PLU110",
            "ro.build.version.release": "16",
            "ro.build.version.sdk": "36",
            "ro.build.display.id": "PLU110_16.0.2.408(CN01)",
            "ro.soc.model": "SM8735",
            "ro.boot.vbmeta.device_state": "unlocked",
            "ro.boot.dynamic_partitions": "true",
            "ro.treble.enabled": "true",
        }
        errors, _, _ = device_validator.validate(values)
        self.assertEqual(errors, [])

    def test_rejects_locked_device(self) -> None:
        values = {
            "ro.product.model": "PLU110",
            "ro.build.version.release": "16",
            "ro.build.version.sdk": "36",
            "ro.build.display.id": "PLU110_16.0.2.408(CN01)",
            "ro.soc.model": "SM8735",
            "ro.boot.flash.locked": "1",
        }
        errors, _, _ = device_validator.validate(values)
        self.assertTrue(any("Bootloader" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
