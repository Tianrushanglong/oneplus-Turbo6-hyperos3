#!/usr/bin/env bash
set -Eeuo pipefail

: "${BASE_ROM_URL:?请在仓库 Secrets 中设置 BASE_ROM_URL}"
: "${DONOR_ROM_URL:?请在仓库 Secrets 中设置 DONOR_ROM_URL}"

for url_name in BASE_ROM_URL DONOR_ROM_URL; do
  url="${!url_name}"
  if [[ "$url" != https://* ]]; then
    echo "错误：$url_name 必须使用 HTTPS。" >&2
    exit 2
  fi
done

mkdir -p inputs

download() {
  local url="$1"
  local output="$2"
  echo "正在下载 $output（URL 不写入日志）..."
  curl --fail --location --silent --show-error \
    --retry 5 --retry-all-errors --connect-timeout 30 \
    --output "$output.part" "$url"
  mv "$output.part" "$output"
}

download "$BASE_ROM_URL" inputs/base.rom
download "$DONOR_ROM_URL" inputs/donor.rom

echo "下载完成："
ls -lh inputs/base.rom inputs/donor.rom
