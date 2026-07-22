#!/usr/bin/env python3
"""Stream-verify an Android full OTA and its update_engine payload hashes."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


CHUNK_SIZE = 8 * 1024 * 1024
PROGRESS_STEP = 512 * 1024 * 1024
MAX_PROPERTIES_SIZE = 2 * 1024 * 1024


@dataclass
class VerificationReport:
    path: str
    archive_size: int = 0
    archive_sha256: str = ""
    product_name: str = ""
    version_name: str = ""
    post_build: str = ""
    post_sdk_level: str = ""
    ota_type: str = ""
    payload_size: int = 0
    payload_sha256: str = ""
    payload_file_hash_base64: str = ""
    payload_metadata_size: int = 0
    payload_metadata_sha256: str = ""
    payload_metadata_hash_base64: str = ""
    payload_magic: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_properties(raw: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def select_entry(archive: zipfile.ZipFile, suffix: str) -> zipfile.ZipInfo | None:
    suffix = suffix.lower()
    return next(
        (entry for entry in archive.infolist() if entry.filename.lower().endswith(suffix)),
        None,
    )


def read_small(archive: zipfile.ZipFile, entry: zipfile.ZipInfo) -> bytes:
    if entry.file_size > MAX_PROPERTIES_SIZE:
        raise ValueError(f"文本条目过大：{entry.filename}")
    return archive.read(entry)


def parse_nonnegative_int(value: str, label: str, errors: list[str]) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        errors.append(f"{label} 不是有效整数")
        return None
    if parsed < 0:
        errors.append(f"{label} 不能为负数")
        return None
    return parsed


def validate_base64_sha256(value: str, label: str, errors: list[str]) -> bytes | None:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        errors.append(f"{label} 不是有效 Base64")
        return None
    if len(decoded) != hashlib.sha256().digest_size:
        errors.append(f"{label} 解码后不是 SHA-256")
        return None
    return decoded


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(
    path: Path,
    expected_product: str | None = None,
    expected_version: str | None = None,
    calculate_archive_hash: bool = True,
    show_progress: bool = False,
) -> VerificationReport:
    report = VerificationReport(path=str(path))
    if not path.is_file():
        report.errors.append("文件不存在")
        return report

    report.archive_size = path.stat().st_size
    if calculate_archive_hash:
        report.archive_sha256 = sha256_file(path)

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        report.errors.append(f"无法打开 ZIP：{exc}")
        return report

    try:
        metadata_entry = select_entry(archive, "meta-inf/com/android/metadata")
        properties_entry = select_entry(archive, "payload_properties.txt")
        payload_entry = select_entry(archive, "payload.bin")

        if metadata_entry is None:
            report.errors.append("缺少 META-INF/com/android/metadata")
        if properties_entry is None:
            report.errors.append("缺少 payload_properties.txt")
        if payload_entry is None:
            report.errors.append("缺少 payload.bin")
        if report.errors:
            return report

        assert metadata_entry and properties_entry and payload_entry
        metadata = parse_properties(read_small(archive, metadata_entry))
        properties = parse_properties(read_small(archive, properties_entry))

        report.product_name = metadata.get("product_name", "")
        report.version_name = metadata.get("version_name", "")
        report.post_build = metadata.get("post-build", "")
        report.post_sdk_level = metadata.get("post-sdk-level", "")
        report.ota_type = metadata.get("ota-type", "")
        report.payload_size = payload_entry.file_size

        if expected_product and report.product_name != expected_product:
            report.errors.append(
                f"product_name 为 {report.product_name or '<空>'}，预期 {expected_product}"
            )
        if expected_version and report.version_name != expected_version:
            report.errors.append(
                f"version_name 为 {report.version_name or '<空>'}，预期 {expected_version}"
            )
        if report.ota_type.upper() != "AB":
            report.errors.append(f"ota-type 为 {report.ota_type or '<空>'}，预期 AB")

        declared_size_raw = properties.get("FILE_SIZE")
        if declared_size_raw is None:
            report.errors.append("payload_properties.txt 缺少 FILE_SIZE")
        else:
            declared_size = parse_nonnegative_int(
                declared_size_raw, "FILE_SIZE", report.errors
            )
            if declared_size is not None and declared_size != payload_entry.file_size:
                report.errors.append(
                    f"payload.bin 大小为 {payload_entry.file_size}，FILE_SIZE 为 {declared_size}"
                )

        expected_file_hash_raw = properties.get("FILE_HASH")
        expected_file_hash = None
        if expected_file_hash_raw is None:
            report.errors.append("payload_properties.txt 缺少 FILE_HASH")
        else:
            expected_file_hash = validate_base64_sha256(
                expected_file_hash_raw, "FILE_HASH", report.errors
            )

        metadata_size_raw = properties.get("METADATA_SIZE")
        metadata_size = None
        if metadata_size_raw is None:
            report.errors.append("payload_properties.txt 缺少 METADATA_SIZE")
        else:
            metadata_size = parse_nonnegative_int(
                metadata_size_raw, "METADATA_SIZE", report.errors
            )
            if metadata_size is not None and metadata_size > payload_entry.file_size:
                report.errors.append("METADATA_SIZE 大于 payload.bin")
                metadata_size = None
        report.payload_metadata_size = metadata_size or 0

        expected_metadata_hash_raw = properties.get("METADATA_HASH")
        expected_metadata_hash = None
        if expected_metadata_hash_raw is None:
            report.errors.append("payload_properties.txt 缺少 METADATA_HASH")
        else:
            expected_metadata_hash = validate_base64_sha256(
                expected_metadata_hash_raw, "METADATA_HASH", report.errors
            )

        payload_digest = hashlib.sha256()
        metadata_digest = hashlib.sha256()
        metadata_remaining = metadata_size or 0
        processed = 0
        next_progress = PROGRESS_STEP
        prefix = bytearray()

        with archive.open(payload_entry, "r") as stream:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                if len(prefix) < 4:
                    prefix.extend(chunk[: 4 - len(prefix)])
                payload_digest.update(chunk)
                if metadata_remaining:
                    metadata_chunk = chunk[:metadata_remaining]
                    metadata_digest.update(metadata_chunk)
                    metadata_remaining -= len(metadata_chunk)
                processed += len(chunk)
                if show_progress and processed >= next_progress:
                    percent = processed * 100 / payload_entry.file_size
                    print(
                        f"payload 校验：{processed / (1024**3):.2f} GiB / "
                        f"{payload_entry.file_size / (1024**3):.2f} GiB ({percent:.1f}%)",
                        file=sys.stderr,
                    )
                    next_progress += PROGRESS_STEP

        report.payload_magic = bytes(prefix).decode("ascii", errors="replace")
        report.payload_sha256 = payload_digest.hexdigest()
        report.payload_file_hash_base64 = base64.b64encode(
            payload_digest.digest()
        ).decode("ascii")
        report.payload_metadata_sha256 = metadata_digest.hexdigest()
        report.payload_metadata_hash_base64 = base64.b64encode(
            metadata_digest.digest()
        ).decode("ascii")

        if processed != payload_entry.file_size:
            report.errors.append(
                f"实际读取 payload.bin {processed} 字节，ZIP 声明 {payload_entry.file_size} 字节"
            )
        if report.payload_magic != "CrAU":
            report.errors.append(f"payload magic 为 {report.payload_magic!r}，预期 'CrAU'")
        if expected_file_hash is not None and payload_digest.digest() != expected_file_hash:
            report.errors.append("payload.bin SHA-256 与 FILE_HASH 不一致")
        if (
            expected_metadata_hash is not None
            and metadata_size is not None
            and metadata_digest.digest() != expected_metadata_hash
        ):
            report.errors.append("payload metadata SHA-256 与 METADATA_HASH 不一致")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        report.errors.append(f"校验 ZIP 失败：{exc}")
    finally:
        archive.close()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="流式校验 Android full OTA 与 payload_properties 中的 SHA-256"
    )
    parser.add_argument("ota", type=Path)
    parser.add_argument("--expected-product")
    parser.add_argument("--expected-version")
    parser.add_argument("--skip-archive-sha256", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    report = verify(
        args.ota,
        expected_product=args.expected_product,
        expected_version=args.expected_version,
        calculate_archive_hash=not args.skip_archive_sha256,
        show_progress=args.progress,
    )
    result = {"ok": report.ok, "format_version": 1, "report": asdict(report)}
    rendered = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"报告已保存：{args.output}")
    else:
        print(rendered)

    for error in report.errors:
        print(f"错误：{error}", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
