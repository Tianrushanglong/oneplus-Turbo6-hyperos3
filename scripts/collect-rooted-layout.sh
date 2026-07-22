#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_MODEL="PLU110"
EXPECTED_BUILD="PLU110_16.0.2.408"
OUTPUT="${1:-rooted-layout-$(date -u +%Y%m%dT%H%M%SZ).txt}"

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
  echo "错误：未检测到已授权的 adb 设备。" >&2
  exit 2
fi

model="$(adb shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
build="$(adb shell getprop ro.build.display.id 2>/dev/null | tr -d '\r')"
incremental="$(adb shell getprop ro.build.version.incremental 2>/dev/null | tr -d '\r')"

if [[ "$model" != "$EXPECTED_MODEL" ]]; then
  echo "拒绝继续：检测到型号 '$model'，预期 '$EXPECTED_MODEL'。" >&2
  exit 3
fi

if [[ "${build} ${incremental}" != *"$EXPECTED_BUILD"* ]]; then
  echo "拒绝继续：检测到版本 '${build:-$incremental}'，预期 '$EXPECTED_BUILD'。" >&2
  exit 4
fi

echo "正在请求 Root 授权；请在手机上允许本次 su 请求。"
root_id="$(adb shell su -c id 2>/dev/null | tr -d '\r' || true)"
if [[ "$root_id" != *"uid=0"* ]]; then
  echo "错误：未获得 Root shell。请确认 Magisk/KernelSU/APatch 已向 shell 授权。" >&2
  exit 5
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

{
  echo "# OnePlus Turbo 6 rooted layout report"
  echo "# Read-only: no reboot, write, erase, dd, or flash command was used."
  echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

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

  for prop in "${props[@]}"; do
    value="$(adb shell getprop "$prop" 2>/dev/null | tr -d '\r')"
    printf '%s=%s\n' "$prop" "$value"
  done

  echo
  echo "[root_check]"
  printf '%s\n' "$root_id"

  echo
  echo "[rooted_partition_inventory]"
  adb shell su -c sh <<'ROOT_SCRIPT' | tr -d '\r'
echo "-- uname --"
uname -a 2>/dev/null || true
cat /proc/version 2>/dev/null || true

echo "-- by-name links --"
ls -l /dev/block/by-name 2>/dev/null || \
  ls -l /dev/block/bootdevice/by-name 2>/dev/null || true

echo "-- mapper links --"
ls -l /dev/block/mapper 2>/dev/null || true

echo "-- physical partition sizes --"
BY_NAME=/dev/block/by-name
[ -d "$BY_NAME" ] || BY_NAME=/dev/block/bootdevice/by-name
if [ -d "$BY_NAME" ]; then
  for node in "$BY_NAME"/*; do
    [ -e "$node" ] || continue
    name=${node##*/}
    size=$(blockdev --getsize64 "$node" 2>/dev/null || echo unknown)
    target=$(readlink -f "$node" 2>/dev/null || echo unknown)
    printf '%s|%s|%s\n' "$name" "$size" "$target"
  done
fi

echo "-- device-mapper names and sizes --"
for sysnode in /sys/class/block/dm-*; do
  [ -d "$sysnode" ] || continue
  block=${sysnode##*/}
  name=$(cat "$sysnode/dm/name" 2>/dev/null || echo unknown)
  size=$(blockdev --getsize64 "/dev/block/$block" 2>/dev/null || echo unknown)
  printf '%s|%s|%s\n' "$name" "$size" "$block"
done

echo "-- lpdump --"
if command -v lpdump >/dev/null 2>&1; then
  lpdump 2>&1 || true
elif [ -x /system/bin/lpdump ]; then
  /system/bin/lpdump 2>&1 || true
else
  echo "lpdump unavailable"
fi

echo "-- avb state --"
if command -v avbctl >/dev/null 2>&1; then
  avbctl get-verity 2>&1 || true
  avbctl get-verification 2>&1 || true
elif [ -x /system/bin/avbctl ]; then
  /system/bin/avbctl get-verity 2>&1 || true
  /system/bin/avbctl get-verification 2>&1 || true
else
  echo "avbctl unavailable"
fi

echo "-- proc partitions --"
cat /proc/partitions 2>/dev/null || true
ROOT_SCRIPT
} >"$tmp"

mv "$tmp" "$OUTPUT"
trap - EXIT

echo "只读分区清单已保存：$OUTPUT"
echo "请把该文件发给移植维护者；报告不包含 IMEI、序列号或分区内容。"
