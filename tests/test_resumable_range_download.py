from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


downloader = load_module(
    "resumable_range_download", ROOT / "tools" / "resumable_range_download.py"
)


class ResumableRangeDownloadTests(unittest.TestCase):
    def test_segments_cover_file_exactly(self) -> None:
        segments = downloader.make_segments(10, 4)
        self.assertEqual(
            [(item.start, item.end, item.size) for item in segments],
            [(0, 3, 4), (4, 7, 4), (8, 9, 2)],
        )

    def test_manifest_accepts_refreshed_url_for_same_etag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            first = downloader.RemoteIdentity(100, '"same"', "yesterday")
            second = downloader.RemoteIdentity(100, '"same"', "today")
            downloader.load_or_create_manifest(path, first, 32)
            resumed = downloader.load_or_create_manifest(path, second, 32)
            self.assertEqual(resumed.etag, '"same"')
            self.assertNotIn("http", path.read_text(encoding="utf-8"))

    def test_manifest_rejects_different_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            downloader.load_or_create_manifest(
                path, downloader.RemoteIdentity(100, '"old"', ""), 32
            )
            with self.assertRaisesRegex(RuntimeError, "ETag"):
                downloader.load_or_create_manifest(
                    path, downloader.RemoteIdentity(100, '"new"', ""), 32
                )

    def test_assemble_is_ordered_and_hashes_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segments = downloader.make_segments(6, 2)
            for segment, content in zip(segments, (b"ab", b"cd", b"ef")):
                (root / segment.filename).write_bytes(content)
            output = root / "result.bin"
            digest = downloader.assemble(output, root, segments)
            self.assertEqual(output.read_bytes(), b"abcdef")
            self.assertEqual(
                digest,
                "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721",
            )


if __name__ == "__main__":
    unittest.main()
