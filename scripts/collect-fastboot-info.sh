#!/usr/bin/env bash
set -Eeuo pipefail

OUTPUT="${1:-fastboot-info-$(date -u +%Y%m%dT%H%M%SZ).txt}"

if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [output-file]" >&2
  exit 64
fi

if ! command -v fastboot >/dev/null 2>&1; then
  echo "错误：找不到 fastboot。请先安装 Android platform-tools。" >&2
  exit 127
fi

devices="$(fastboot devices 2>/dev/null || true)"
if [[ -z "$devices" ]]; then
  echo "错误：没有检测到 fastboot 设备。请先手动进入 bootloader。" >&2
  exit 2
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

{
  echo "# OnePlus Turbo 6 fastboot report (read-only)"
  echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[devices]"
  printf '%s\n' "$devices"

  for variable in product current-slot slot-count unlocked secure anti is-userspace; do
    echo
    echo "[getvar:$variable]"
    fastboot getvar "$variable" 2>&1 || true
  done

  echo
  echo "[oem-device-info]"
  fastboot oem device-info 2>&1 || true
} >"$tmp"

mv "$tmp" "$OUTPUT"
trap - EXIT
echo "报告已保存：$OUTPUT"
echo "脚本未执行解锁、擦除或刷写命令。"
