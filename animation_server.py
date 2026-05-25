#!/usr/bin/env python3
"""Local web server for the animated image converter."""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from animation_converter import (
    CancelledError,
    ConversionResult,
    ConversionSettings,
    Converter,
    FORMAT_LABELS,
    PRESETS,
    format_bytes,
    probe_video,
)


ROOT = Path(__file__).resolve().parent
WEB_DIST = ROOT / "webui" / "dist"
DATA_ROOT = Path(os.environ.get("ANIMATION_DATA_DIR", ROOT)).expanduser().resolve()
UPLOADS = DATA_ROOT / "uploads"
OUTPUTS = DATA_ROOT / "outputs"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}
MEDIA_MIMES = {
    ".apng": "image/apng",
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}
PUBLIC_MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | {".apng", ".avif", ".gif", ".webp"}


@dataclass
class Job:
    id: str
    source: Path
    formats: tuple[str, ...]
    target_bytes: int | None
    status: str = "queued"
    logs: list[str] = field(default_factory=list)
    results: list[ConversionResult] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_log(self, message: str) -> None:
        with self.lock:
            self.logs.append(message)


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def stored_reference(path: Path) -> str:
    path = path.resolve()
    locations = (("root", ROOT),) if DATA_ROOT == ROOT else (("data", DATA_ROOT), ("root", ROOT))
    for scope, parent in locations:
        if is_within(path, parent):
            relative = path.relative_to(parent).as_posix()
            return f"{scope}/{relative}"
    raise ValueError("文件不在允许的存储目录中。")


def stored_path(reference: str) -> Path:
    scope, separator, relative = reference.partition("/")
    if separator and scope in {"root", "data"}:
        parent = ROOT if scope == "root" else DATA_ROOT
    else:
        parent = ROOT
        relative = reference
    candidate = (parent / relative).resolve()
    if not is_within(candidate, parent):
        raise ValueError("文件路径无效。")
    return candidate


def media_url(path: Path) -> str:
    return "/media/" + quote(stored_reference(path))


def allowed_input(relative: str) -> Path:
    path = stored_path(relative)
    if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
        raise ValueError("输入文件不存在或不是支持的视频格式。")
    if path.parent == ROOT or is_within(path, UPLOADS):
        return path
    raise ValueError("仅允许转换工作目录或上传目录中的视频。")


def video_item(path: Path) -> dict[str, object]:
    info = probe_video(path)
    return {
        "path": stored_reference(path),
        "name": path.name,
        "url": media_url(path),
        "size": path.stat().st_size,
        "sizeText": format_bytes(path.stat().st_size),
        "width": info.width,
        "height": info.height,
        "fps": round(info.fps, 2),
        "duration": round(info.duration, 2),
    }


def list_videos() -> list[dict[str, object]]:
    candidates = [
        path for path in ROOT.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if UPLOADS.exists():
        candidates.extend(
            path for path in UPLOADS.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
    items = []
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            items.append(video_item(path))
        except RuntimeError:
            continue
    return items


def result_item(result: ConversionResult, source_info: object) -> dict[str, object]:
    height = round(source_info.height * result.params.width / source_info.width)
    if height % 2:
        height += 1
    return {
        "format": result.fmt,
        "label": FORMAT_LABELS[result.fmt],
        "url": media_url(result.path),
        "name": result.path.name,
        "size": result.size,
        "sizeText": format_bytes(result.size),
        "width": result.params.width,
        "height": height,
        "fps": round(result.params.fps, 2),
        "colors": result.params.colors,
        "webpQuality": result.params.webp_quality,
        "avifCrf": result.params.avif_crf,
    }


def serialized_job(job: Job) -> dict[str, object]:
    with job.lock:
        info = probe_video(job.source) if job.results else None
        return {
            "id": job.id,
            "status": job.status,
            "source": job.source.name,
            "formats": list(job.formats),
            "targetBytes": job.target_bytes,
            "logs": list(job.logs),
            "results": [result_item(result, info) for result in job.results] if info else [],
            "error": job.error,
        }


def preview_document(job: Job) -> bytes:
    with job.lock:
        if job.status != "done" or not job.results:
            raise ValueError("转换尚未完成，暂时无法查看对比页。")
        results = list(job.results)
        source = job.source
    info = probe_video(source)
    cards = []
    for result in results:
        item = result_item(result, info)
        detail = f"{item['sizeText']} | {item['width']} x {item['height']} | {item['fps']} fps"
        cards.append(
            f"""<article><div class="title"><b>{html.escape(str(item['label']))}</b>
            <span>{html.escape(detail)}</span><a href="{html.escape(str(item['url']))}" download>下载</a></div>
            <div class="stage"><img src="{html.escape(str(item['url']))}" alt="{html.escape(str(item['label']))}"></div></article>"""
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(source.stem)} - 动图对比</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0f1217;color:#f2f4f8;font:14px -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei UI","Segoe UI",sans-serif}}
header{{position:sticky;top:0;z-index:1;padding:18px 24px;background:#0f1217eb;border-bottom:1px solid #29303d;backdrop-filter:blur(15px)}}
h1{{font-size:22px;margin:0 0 6px}}p{{margin:0;color:#aab3c2}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px;padding:18px}}
article{{background:#171c24;border:1px solid #2a3342;border-radius:14px;overflow:hidden}}.title{{display:flex;align-items:center;gap:12px;padding:12px 14px;border-bottom:1px solid #2a3342}}
.title b{{font-size:18px}}.title span{{color:#aab3c2;flex:1}}.title a{{color:#64adff;text-decoration:none}}.stage{{height:min(72vh,760px);padding:10px;background:#080a0e;display:flex;justify-content:center}}
img{{width:100%;height:100%;object-fit:contain}}
</style></head><body><header><h1>动图转换效果对比</h1><p>输入文件：{html.escape(source.name)}；所有输出同步循环播放。</p></header>
<main>{''.join(cards)}</main></body></html>"""
    return document.encode("utf-8")


def run_job(job: Job, settings: ConversionSettings) -> None:
    job.status = "running"
    try:
        converter = Converter(job.source, settings, job.add_log, job.cancel)
        results, _preview = converter.convert_all()
        with job.lock:
            job.results = results
            job.status = "done"
    except CancelledError:
        with job.lock:
            job.status = "cancelled"
            job.logs.append("操作已取消。")
    except Exception as exc:
        with job.lock:
            job.status = "error"
            job.error = str(exc)
            job.logs.append("错误：" + str(exc))


class AppHandler(BaseHTTPRequestHandler):
    server_version = "AnimationConverter/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def json_response(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", 0))
        if length > 1_000_000:
            raise ValueError("请求内容过大。")
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route == "/api/health":
            self.json_response({"ok": True})
            return
        if route == "/api/config":
            self.json_response(
                {
                    "presets": PRESETS,
                    "formats": [
                        {"value": key, "label": label} for key, label in FORMAT_LABELS.items()
                    ],
                    "videos": list_videos(),
                }
            )
            return
        if route == "/api/videos":
            self.json_response({"videos": list_videos()})
            return
        if route.startswith("/api/jobs/"):
            job_id = route.removeprefix("/api/jobs/").split("/", 1)[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self.json_response({"error": "任务不存在。"}, HTTPStatus.NOT_FOUND)
                return
            self.json_response(serialized_job(job))
            return
        if route.startswith("/preview/"):
            self.send_preview(route.removeprefix("/preview/").split("/", 1)[0])
            return
        if route.startswith("/media/"):
            self.send_media(route.removeprefix("/media/"))
            return
        self.send_frontend(route)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        try:
            if route == "/api/upload":
                self.handle_upload()
                return
            if route == "/api/jobs":
                self.handle_new_job()
                return
            if route.startswith("/api/jobs/") and route.endswith("/cancel"):
                job_id = route.removeprefix("/api/jobs/").removesuffix("/cancel").strip("/")
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                if not job:
                    self.json_response({"error": "任务不存在。"}, HTTPStatus.NOT_FOUND)
                    return
                job.cancel.set()
                self.json_response({"ok": True})
                return
            self.json_response({"error": "接口不存在。"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_upload(self) -> None:
        name = Path(unquote(self.headers.get("X-File-Name", ""))).name
        if not name or Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError("请选择支持的视频文件。")
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            raise ValueError("上传文件为空。")
        UPLOADS.mkdir(parents=True, exist_ok=True)
        unique = f"{int(time.time())}_{name}"
        destination = UPLOADS / unique
        remaining = length
        with destination.open("wb") as output:
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("上传中断。")
                output.write(chunk)
                remaining -= len(chunk)
        self.json_response({"video": video_item(destination)}, HTTPStatus.CREATED)

    def handle_new_job(self) -> None:
        data = self.read_json()
        source = allowed_input(str(data.get("source", "")))
        formats = tuple(str(value) for value in data.get("formats", []))
        if not formats or set(formats) - set(FORMAT_LABELS):
            raise ValueError("请选择有效的输出格式。")
        target_mb = data.get("targetMb")
        target_bytes = int(float(target_mb) * 1_000_000) if target_mb else None
        if target_bytes is not None and target_bytes <= 0:
            raise ValueError("目标大小必须大于 0。")
        job_id = uuid.uuid4().hex[:12]
        output_dir = OUTPUTS / job_id
        settings = ConversionSettings(
            formats=formats,
            output_dir=output_dir,
            max_width=max(0, int(data.get("maxWidth", 0))),
            max_fps=max(0.0, float(data.get("maxFps", 0))),
            colors=min(256, max(16, int(data.get("colors", 256)))),
            webp_quality=min(100, max(0, int(data.get("webpQuality", 90)))),
            avif_crf=min(63, max(0, int(data.get("avifCrf", 16)))),
            speed=min(8, max(0, int(data.get("speed", 3)))),
            target_bytes=target_bytes,
            auto_optimize=bool(data.get("autoOptimize", True)),
            make_preview=False,
        )
        job = Job(job_id, source, formats, target_bytes)
        with JOBS_LOCK:
            JOBS[job_id] = job
        thread = threading.Thread(target=run_job, args=(job, settings), daemon=True)
        thread.start()
        self.json_response(serialized_job(job), HTTPStatus.ACCEPTED)

    def send_media(self, raw_relative: str) -> None:
        try:
            candidate = stored_path(unquote(raw_relative))
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if (
            not candidate.is_file()
            or candidate.suffix.lower() not in PUBLIC_MEDIA_EXTENSIONS
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime = MEDIA_MIMES.get(candidate.suffix.lower()) or mimetypes.guess_type(candidate.name)[0]
        content_type = mime or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(candidate.stat().st_size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with candidate.open("rb") as content:
            while chunk := content.read(1024 * 1024):
                self.wfile.write(chunk)

    def send_preview(self, job_id: str) -> None:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job:
            self.send_error(HTTPStatus.NOT_FOUND, "任务不存在。")
            return
        try:
            body = preview_document(job)
        except ValueError as exc:
            self.send_error(HTTPStatus.CONFLICT, str(exc))
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_frontend(self, route: str) -> None:
        if not WEB_DIST.exists():
            body = (
                "前端尚未构建。请先在 webui 目录运行 npm.cmd install 和 npm.cmd run build。"
            ).encode("utf-8")
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        relative = route.lstrip("/") or "index.html"
        candidate = (WEB_DIST / relative).resolve()
        if not is_within(candidate, WEB_DIST) or not candidate.is_file():
            candidate = WEB_DIST / "index.html"
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(candidate.stat().st_size))
        self.end_headers()
        self.wfile.write(candidate.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description="启动动图转换器网页服务。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器。")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"动图转换器已启动：{url}")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
