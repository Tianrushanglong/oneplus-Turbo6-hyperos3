#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_MODEL="PLU110"
OUTPUT="${1:-device-info-$(date -u +%Y%m%dT%H%M%SZ).txt}"

if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [output-file]" >&2
  exit 64
fi

if ! command -v adb >/dev/null 2>&1; then
  echo "错误：找不到 adb。请先安装 Android platform-tools。" >&2
  exit 127
fi

adb start-server >/dev/null
if [[ "$(adb get-state 2>/dev/null || true)" != "device" ]]; then
  echo "错误：未检测到已授权的 adb 设备。请连接手机并确认 USB 调试授权。" >&2
  exit 2
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

props=(
  ro.product.brand
  ro.product.manufacturer
  ro.product.model
  ro.product.device
  ro.product.name
  ro.product.product.model
  ro.product.product.device
  ro.product.vendor.device
  ro.product.board
  ro.board.platform
  ro.soc.manufacturer
  ro.soc.model
  ro.build.display.id
  ro.build.fingerprint
  ro.build.version.release
  ro.build.version.sdk
  ro.build.version.incremental
  ro.build.version.security_patch
  ro.vendor.build.security_patch
  ro.product.first_api_level
  ro.vendor.api_level
  ro.vndk.version
  ro.treble.enabled
  ro.boot.dynamic_partitions
  ro.build.ab_update
  ro.boot.slot_suffix
  ro.boot.vbmeta.device_state
  ro.boot.verifiedbootstate
  ro.boot.flash.locked
  ro.boot.avb_version
  ro.boot.boot_devices
  ro.boot.bootloader
  ro.crypto.state
  ro.crypto.type
)

{
  echo "# OnePlus Turbo 6 device report"
  echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for prop in "${props[@]}"; do
    value="$(adb shell getprop "$prop" 2>/dev/null | tr -d '\r')"
    printf '%s=%s\n' "$prop" "$value"
  done

  echo
  echo "[uname]"
  adb shell uname -a 2>/dev/null | tr -d '\r' || true

  echo
  echo "[proc_version]"
  adb shell cat /proc/version 2>/dev/null | tr -d '\r' || true

  echo
  echo "[block_by_name]"
  adb shell 'ls -l /dev/block/by-name 2>/dev/null || ls -l /dev/block/bootdevice/by-name 2>/dev/null || true' | tr -d '\r'

  echo
  echo "[proc_partitions]"
  adb shell cat /proc/partitions 2>/dev/null | tr -d '\r' || true

  echo
  echo "[mounts]"
  adb shell cat /proc/mounts 2>/dev/null | tr -d '\r' || true
} >"$tmp"

mv "$tmp" "$OUTPUT"
trap - EXIT

model="$(awk -F= '$1 == "ro.product.model" {print $2; exit}' "$OUTPUT")"
echo "报告已保存：$OUTPUT"

if [[ "$model" != "$EXPECTED_MODEL" ]]; then
  echo "拒绝继续：检测到型号 '$model'，预期为 '$EXPECTED_MODEL'。报告已保留供排查。" >&2
  exit 3
fi

echo "型号校验通过：$EXPECTED_MODEL"
