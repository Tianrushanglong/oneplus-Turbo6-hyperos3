#!/usr/bin/env python3
"""Validate a read-only report produced by collect-device-info.sh."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


EXPECTED_MODEL = "PLU110"
EXPECTED_ANDROID = "16"
EXPECTED_SDK = "36"
PLATFORM_PATTERN = re.compile(r"(?:sm8735|\bsun\b)", re.IGNORECASE)


def parse_report(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw_line or raw_line.startswith(("#", "[")) or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def first(values: dict[str, str], *keys: str) -> str:
    return next((values[key] for key in keys if values.get(key)), "")


def validate(values: dict[str, str]) -> tuple[list[str], list[str], dict[str, str]]:
    errors: list[str] = []
    warnings: list[str] = []

    model = first(values, "ro.product.model", "ro.product.product.model")
    android = values.get("ro.build.version.release", "")
    sdk = values.get("ro.build.version.sdk", "")
    platform_text = " ".join(
        values.get(key, "")
        for key in ("ro.soc.model", "ro.board.platform", "ro.product.board")
    )
    boot_state = values.get("ro.boot.vbmeta.device_state", "").lower()
    flash_locked = values.get("ro.boot.flash.locked", "")
    dynamic = values.get("ro.boot.dynamic_partitions", "").lower()
    treble = values.get("ro.treble.enabled", "").lower()

    if model != EXPECTED_MODEL:
        errors.append(f"型号不匹配：检测到 {model or '<缺失>'}，预期 {EXPECTED_MODEL}")
    if android != EXPECTED_ANDROID:
        errors.append(f"Android 版本不匹配：检测到 {android or '<缺失>'}，预期 {EXPECTED_ANDROID}")
    if sdk and sdk != EXPECTED_SDK:
        errors.append(f"SDK 不匹配：检测到 {sdk}，预期 {EXPECTED_SDK}")
    if platform_text.strip() and not PLATFORM_PATTERN.search(platform_text):
        errors.append(f"SoC/平台不匹配：{platform_text.strip()}")
    elif not platform_text.strip():
        warnings.append("报告未暴露 SoC/平台属性，需要从 bootloader 或原厂包再次确认")

    if boot_state == "locked" or flash_locked == "1":
        errors.append("Bootloader 仍处于锁定状态，禁止进行移植刷写")
    elif boot_state != "unlocked" and flash_locked != "0":
        warnings.append("无法从报告确认 Bootloader 已解锁")

    if dynamic == "false":
        errors.append("设备报告显示未启用动态分区，与预期布局不符")
    elif not dynamic:
        warnings.append("ro.boot.dynamic_partitions 为空，稍后需用 super 元数据确认")

    if treble == "false":
        errors.append("Treble 被报告为关闭，无法采用当前移植路线")
    elif not treble:
        warnings.append("ro.treble.enabled 为空，需要从 VINTF 文件确认")

    summary = {
        "model": model,
        "android": android,
        "sdk": sdk,
        "platform": platform_text.strip(),
        "boot_state": boot_state or ("unlocked" if flash_locked == "0" else "unknown"),
        "build": values.get("ro.build.display.id", ""),
        "incremental": values.get("ro.build.version.incremental", ""),
        "security_patch": values.get("ro.build.version.security_patch", ""),
    }
    return errors, warnings, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    if not args.report.is_file():
        print(f"错误：报告不存在：{args.report}", file=sys.stderr)
        return 2

    values = parse_report(args.report)
    errors, warnings, summary = validate(values)
    result = {"ok": not errors, "summary": summary, "warnings": warnings, "errors": errors}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"设备：{summary['model'] or '<未知>'}")
        print(f"系统：Android {summary['android'] or '<未知>'} / {summary['build'] or '<未知构建>'}")
        print(f"Bootloader：{summary['boot_state']}")
        for warning in warnings:
            print(f"警告：{warning}")
        for error in errors:
            print(f"错误：{error}", file=sys.stderr)
        print("校验通过" if not errors else "校验失败")

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
