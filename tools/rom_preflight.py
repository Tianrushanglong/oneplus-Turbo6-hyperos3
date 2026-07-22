#!/usr/bin/env python3
"""Inspect target and donor ROM archives without extracting partition images."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol


MAX_TEXT_SIZE = 2 * 1024 * 1024


class ArchiveReader(Protocol):
    kind: str

    def names(self) -> list[str]: ...
    def read_small(self, name: str) -> bytes: ...
    def uncompressed_size(self, name: str) -> int | None: ...
    def close(self) -> None: ...


class ZipReader:
    kind = "zip"

    def __init__(self, path: Path):
        self.archive = zipfile.ZipFile(path)

    def names(self) -> list[str]:
        return self.archive.namelist()

    def read_small(self, name: str) -> bytes:
        info = self.archive.getinfo(name)
        if info.file_size > MAX_TEXT_SIZE:
            raise ValueError(f"文本条目过大：{name}")
        return self.archive.read(name)

    def uncompressed_size(self, name: str) -> int | None:
        return self.archive.getinfo(name).file_size

    def close(self) -> None:
        self.archive.close()


class TarReader:
    kind = "tar"

    def __init__(self, path: Path):
        self.archive = tarfile.open(path, "r:*")
        self.members = {member.name: member for member in self.archive.getmembers() if member.isfile()}

    def names(self) -> list[str]:
        return list(self.members)

    def read_small(self, name: str) -> bytes:
        member = self.members[name]
        if member.size > MAX_TEXT_SIZE:
            raise ValueError(f"文本条目过大：{name}")
        extracted = self.archive.extractfile(member)
        return extracted.read() if extracted else b""

    def uncompressed_size(self, name: str) -> int | None:
        return self.members[name].size

    def close(self) -> None:
        self.archive.close()


@dataclass
class RomReport:
    role: str
    path: str
    archive_type: str = "unknown"
    file_size: int = 0
    sha256: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    has_payload: bool = False
    payload_size: int | None = None
    has_super_image: bool = False
    notable_entries: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_archive(path: Path) -> ArchiveReader:
    if zipfile.is_zipfile(path):
        return ZipReader(path)
    if tarfile.is_tarfile(path):
        return TarReader(path)
    raise ValueError("不是受支持的 ZIP/TAR/TGZ ROM 包")


def parse_properties(raw: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def select_entry(names: list[str], suffix: str) -> str | None:
    suffix = suffix.lower()
    return next((name for name in names if name.lower().endswith(suffix)), None)


def inspect(path: Path, role: str, expected_sha256: str | None = None) -> RomReport:
    report = RomReport(role=role, path=str(path), file_size=path.stat().st_size)
    report.sha256 = sha256_file(path)
    if expected_sha256 and report.sha256.lower() != expected_sha256.lower():
        report.errors.append("SHA-256 与预期值不一致")

    try:
        archive = open_archive(path)
    except (ValueError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        report.errors.append(str(exc))
        return report

    try:
        report.archive_type = archive.kind
        names = archive.names()
        lowered = {name.lower(): name for name in names}

        metadata_name = select_entry(names, "meta-inf/com/android/metadata")
        if metadata_name:
            report.metadata.update(parse_properties(archive.read_small(metadata_name)))
        else:
            report.warnings.append("未找到 META-INF/com/android/metadata")

        payload_props_name = select_entry(names, "payload_properties.txt")
        if payload_props_name:
            for key, value in parse_properties(archive.read_small(payload_props_name)).items():
                report.metadata[f"payload.{key}"] = value

        payload_name = select_entry(names, "payload.bin")
        if payload_name:
            report.has_payload = True
            report.payload_size = archive.uncompressed_size(payload_name)
            declared_size = report.metadata.get("payload.FILE_SIZE")
            if declared_size and report.payload_size is not None:
                try:
                    if int(declared_size) != report.payload_size:
                        report.errors.append("payload.bin 大小与 payload_properties.txt 不一致")
                except ValueError:
                    report.warnings.append("payload.FILE_SIZE 不是有效整数")

        super_names = [
            original
            for lower, original in lowered.items()
            if lower.endswith(("/super.img", "super.img", "/super.img_sparsechunk.0"))
        ]
        report.has_super_image = bool(super_names)

        interesting_suffixes = (
            "payload.bin",
            "payload_properties.txt",
            "super.img",
            "boot.img",
            "init_boot.img",
            "vendor_boot.img",
            "vbmeta.img",
            "dtbo.img",
        )
        report.notable_entries = sorted(
            name for name in names if name.lower().endswith(interesting_suffixes)
        )[:100]

        if not report.has_payload and not report.has_super_image:
            report.errors.append("包内既没有 payload.bin，也没有 super.img")
    except (KeyError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        report.errors.append(f"读取归档失败：{exc}")
    finally:
        archive.close()

    identity = " ".join(
        [path.name, *report.metadata.values(), *report.notable_entries]
    ).lower()
    sdk = report.metadata.get("post-sdk-level", "")

    if role == "base":
        if "plu110" not in identity:
            report.errors.append("无法从包名或 OTA 元数据确认 base 属于 PLU110")
        if "16.0.2.408" not in identity:
            report.errors.append("base 不是已锁定的 PLU110_16.0.2.408 完整包")
    elif role == "donor":
        if "onyx" not in identity:
            report.errors.append("无法从包名或 OTA 元数据确认 donor 代号为 onyx")
        if "os3" not in identity and "hyperos 3" not in identity:
            report.errors.append("无法确认 donor 为 HyperOS 3")

    if sdk and sdk != "36":
        report.errors.append(f"Android SDK 为 {sdk}，预期 Android 16 / SDK 36")
    elif not sdk:
        report.warnings.append("OTA 元数据未声明 post-sdk-level，需要解包后再次校验 Android 版本")

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=Path, help="PLU110 full OTA/fastboot package")
    parser.add_argument("--donor", required=True, type=Path, help="onyx HyperOS 3 full package")
    parser.add_argument("--base-sha256")
    parser.add_argument("--donor-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    missing = [str(path) for path in (args.base, args.donor) if not path.is_file()]
    if missing:
        print("错误：文件不存在：" + ", ".join(missing), file=sys.stderr)
        return 2

    reports = [
        inspect(args.base, "base", args.base_sha256),
        inspect(args.donor, "donor", args.donor_sha256),
    ]
    result = {
        "ok": all(not report.errors for report in reports),
        "format_version": 1,
        "reports": [asdict(report) for report in reports],
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"报告已保存：{args.output}")
    else:
        print(rendered)

    for report in reports:
        print(f"{report.role}: {report.sha256} ({report.archive_type})")
        for warning in report.warnings:
            print(f"警告 [{report.role}]：{warning}")
        for error in report.errors:
            print(f"错误 [{report.role}]：{error}", file=sys.stderr)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
