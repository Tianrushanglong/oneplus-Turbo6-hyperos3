#!/usr/bin/env python3
"""Download large ROM files with persistent, resumable HTTP Range segments.

The URL is read from an environment variable so signed query parameters are not
stored in scripts, manifests, command-line arguments, or process listings.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path


MIB = 1024 * 1024
DEFAULT_SEGMENT_SIZE = 64 * MIB
READ_SIZE = 2 * MIB
CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


@dataclass(frozen=True)
class RemoteIdentity:
    total_size: int
    etag: str
    last_modified: str


@dataclass(frozen=True)
class DownloadManifest:
    format_version: int
    total_size: int
    etag: str
    last_modified: str
    segment_size: int


@dataclass(frozen=True)
class Segment:
    index: int
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    @property
    def filename(self) -> str:
        return f"part-{self.index:05d}-{self.start}-{self.end}"


def request_headers(start: int, end: int) -> dict[str, str]:
    return {
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Range": f"bytes={start}-{end}",
        "User-Agent": "PLU110-port-resumable-downloader/1",
    }


def parse_content_range(value: str) -> tuple[int, int, int]:
    match = CONTENT_RANGE_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("服务器返回了无效的 Content-Range")
    return tuple(int(item) for item in match.groups())


def open_range(url: str, start: int, end: int, timeout: float):
    request = urllib.request.Request(url, headers=request_headers(start, end))
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        label = type(reason).__name__ if reason is not None else "network error"
        raise RuntimeError(f"网络错误：{label}") from None

    status = getattr(response, "status", response.getcode())
    if status != 206:
        response.close()
        raise RuntimeError(f"服务器未接受 Range 请求（HTTP {status}）")

    try:
        actual_start, actual_end, total = parse_content_range(
            response.headers.get("Content-Range", "")
        )
    except ValueError:
        response.close()
        raise
    if actual_start != start or actual_end != end:
        response.close()
        raise RuntimeError(
            f"Range 不匹配：请求 {start}-{end}，返回 {actual_start}-{actual_end}"
        )
    return response, total


def probe(url: str, timeout: float) -> RemoteIdentity:
    response, total = open_range(url, 0, 0, timeout)
    try:
        if response.read(1) == b"":
            raise RuntimeError("远端文件为空")
        return RemoteIdentity(
            total_size=total,
            etag=response.headers.get("ETag", ""),
            last_modified=response.headers.get("Last-Modified", ""),
        )
    finally:
        response.close()


def make_segments(total_size: int, segment_size: int) -> list[Segment]:
    if total_size <= 0:
        raise ValueError("远端文件大小必须大于 0")
    if segment_size <= 0:
        raise ValueError("segment_size 必须大于 0")
    result = []
    for index, start in enumerate(range(0, total_size, segment_size)):
        result.append(
            Segment(index=index, start=start, end=min(start + segment_size, total_size) - 1)
        )
    return result


def manifest_from_remote(
    remote: RemoteIdentity, segment_size: int
) -> DownloadManifest:
    return DownloadManifest(
        format_version=1,
        total_size=remote.total_size,
        etag=remote.etag,
        last_modified=remote.last_modified,
        segment_size=segment_size,
    )


def load_or_create_manifest(
    path: Path, remote: RemoteIdentity, segment_size: int
) -> DownloadManifest:
    expected = manifest_from_remote(remote, segment_size)
    if not path.exists():
        path.write_text(
            json.dumps(asdict(expected), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return expected

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        existing = DownloadManifest(**raw)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取已有下载清单：{exc}") from exc

    if existing.format_version != expected.format_version:
        raise RuntimeError("已有下载清单版本不受支持")
    if existing.total_size != expected.total_size:
        raise RuntimeError("新链接的文件大小与已有分段不一致，拒绝续传")
    if existing.segment_size != expected.segment_size:
        raise RuntimeError("续传时不能修改 segment size")
    if existing.etag and expected.etag and existing.etag != expected.etag:
        raise RuntimeError("新链接的 ETag 与已有分段不一致，拒绝续传")
    if (
        not existing.etag
        and existing.last_modified
        and expected.last_modified
        and existing.last_modified != expected.last_modified
    ):
        raise RuntimeError("新链接的 Last-Modified 与已有分段不一致，拒绝续传")
    return existing


def existing_bytes(parts_dir: Path, segments: list[Segment]) -> int:
    total = 0
    for segment in segments:
        path = parts_dir / segment.filename
        if path.exists():
            size = path.stat().st_size
            if size > segment.size:
                raise RuntimeError(f"分段尺寸异常：{path.name}")
            total += size
    return total


def download_segment(
    url: str,
    parts_dir: Path,
    segment: Segment,
    total_size: int,
    timeout: float,
    retries: int,
) -> int:
    path = parts_dir / segment.filename
    for attempt in range(retries + 1):
        current = path.stat().st_size if path.exists() else 0
        if current > segment.size:
            raise RuntimeError(f"分段尺寸异常：{path.name}")
        if current == segment.size:
            return current

        requested_start = segment.start + current
        try:
            response, reported_total = open_range(
                url, requested_start, segment.end, timeout
            )
            if reported_total != total_size:
                response.close()
                raise RuntimeError("下载中远端文件大小发生变化")
            try:
                with path.open("ab") as stream:
                    while True:
                        chunk = response.read(READ_SIZE)
                        if not chunk:
                            break
                        stream.write(chunk)
            finally:
                response.close()

            current = path.stat().st_size
            if current == segment.size:
                return current
            if current > segment.size:
                raise RuntimeError(f"服务器返回了过多数据：{path.name}")
            raise RuntimeError(f"连接提前结束：{path.name}")
        except (OSError, RuntimeError, ValueError) as exc:
            if attempt >= retries:
                raise RuntimeError(f"{path.name}：{exc}") from None
            time.sleep(min(2**attempt, 5))
    raise AssertionError("unreachable")


def assemble(output: Path, parts_dir: Path, segments: list[Segment]) -> str:
    temporary = output.with_name(output.name + ".assembling")
    digest = hashlib.sha256()
    with temporary.open("wb") as destination:
        for segment in segments:
            path = parts_dir / segment.filename
            if not path.is_file() or path.stat().st_size != segment.size:
                raise RuntimeError(f"分段不完整：{path.name}")
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(READ_SIZE), b""):
                    destination.write(chunk)
                    digest.update(chunk)
        destination.flush()
        os.fsync(destination.fileno())

    expected_size = sum(segment.size for segment in segments)
    if temporary.stat().st_size != expected_size:
        raise RuntimeError("合并后的文件大小不正确")
    temporary.replace(output)
    return digest.hexdigest()


def run(args: argparse.Namespace) -> int:
    url = os.environ.get(args.url_env, "").strip()
    if not url:
        print(f"错误：环境变量 {args.url_env} 为空", file=sys.stderr)
        return 2
    if args.output.exists():
        print(f"目标文件已存在，拒绝覆盖：{args.output}", file=sys.stderr)
        return 2

    try:
        remote = probe(url, args.timeout)
        parts_dir = args.parts_dir or args.output.parent / f".{args.output.name}.parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        manifest = load_or_create_manifest(
            parts_dir / "manifest.json", remote, args.segment_mib * MIB
        )
        segments = make_segments(manifest.total_size, manifest.segment_size)
        downloaded = existing_bytes(parts_dir, segments)
        print(
            f"远端大小：{manifest.total_size / (1024**3):.2f} GiB；"
            f"已保留：{downloaded / (1024**3):.2f} GiB；分段：{len(segments)}"
        )

        pending = [
            segment
            for segment in segments
            if not (parts_dir / segment.filename).exists()
            or (parts_dir / segment.filename).stat().st_size != segment.size
        ]
        completed_bytes = downloaded
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {
                executor.submit(
                    download_segment,
                    url,
                    parts_dir,
                    segment,
                    manifest.total_size,
                    args.timeout,
                    args.retries,
                ): segment
                for segment in pending
            }
            for future in concurrent.futures.as_completed(futures):
                segment = futures[future]
                future.result()
                # Recompute so partial bytes preserved by failed earlier attempts are counted once.
                completed_bytes = existing_bytes(parts_dir, segments)
                percent = completed_bytes * 100 / manifest.total_size
                print(
                    f"下载进度：{completed_bytes / (1024**3):.2f} / "
                    f"{manifest.total_size / (1024**3):.2f} GiB ({percent:.1f}%)"
                )

        sha256 = assemble(args.output, parts_dir, segments)
        print(f"下载完成：{args.output}")
        print(f"SHA-256：{sha256}")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        print("已下载的分段仍被保留，可用同一文件的新签名链接续传。", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="可续传、不会记录签名 URL 的 ROM 下载器")
    parser.add_argument("output", type=Path)
    parser.add_argument("--url-env", default="ROM_URL")
    parser.add_argument("--parts-dir", type=Path)
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--segment-mib", type=int, default=64)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.jobs <= 0 or args.segment_mib <= 0 or args.retries < 0 or args.timeout <= 0:
        parser.error("jobs、segment-mib、timeout 必须大于 0，retries 不能为负数")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
