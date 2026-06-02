#!/usr/bin/env python3
"""Local animated image converter for GIF, WebP, APNG and AVIF."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable


APP_TITLE = "动图压制转换器"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VIDEO_TYPES = [
    ("视频文件", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv"),
    ("所有文件", "*.*"),
]


def load_tkinter() -> None:
    global filedialog, messagebox, tk, ttk
    import tkinter as tk_module
    from tkinter import filedialog as filedialog_module
    from tkinter import messagebox as messagebox_module
    from tkinter import ttk as ttk_module

    tk = tk_module
    filedialog = filedialog_module
    messagebox = messagebox_module
    ttk = ttk_module
FORMAT_LABELS = {
    "gif": "GIF",
    "webp": "WebP",
    "apng": "APNG",
    "avif": "AVIF",
}
PRESETS = {
    "low": {
        "label": "低压缩（质量优先）",
        "width": 0,
        "fps": 0,
        "colors": 256,
        "webp_quality": 90,
        "avif_crf": 16,
        "speed": 3,
    },
    "medium": {
        "label": "中压缩（均衡）",
        "width": 1200,
        "fps": 20,
        "colors": 192,
        "webp_quality": 78,
        "avif_crf": 26,
        "speed": 5,
    },
    "high": {
        "label": "高压缩（体积优先）",
        "width": 720,
        "fps": 12,
        "colors": 128,
        "webp_quality": 64,
        "avif_crf": 34,
        "speed": 7,
    },
}
PRESET_BY_LABEL = {item["label"]: key for key, item in PRESETS.items()}
AVIF_AUTO_START_WIDTH = 1200
AVIF_AUTO_START_FPS = 15
AVIF_AUTO_MAX_WIDTH = 1600
AVIF_AUTO_MAX_FPS = 20
WEBP_AUTO_START_WIDTH = 900
WEBP_AUTO_START_FPS = 15
WEBP_AUTO_QUALITY_DROP = 4


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    duration: float


@dataclass(frozen=True)
class EncodeParams:
    width: int
    fps: float
    colors: int
    webp_quality: int
    avif_crf: int
    speed: int

    def key(self) -> tuple[int, float, int, int, int]:
        return (
            self.width,
            round(self.fps, 3),
            self.colors,
            self.webp_quality,
            self.avif_crf,
        )


@dataclass(frozen=True)
class ConversionSettings:
    formats: tuple[str, ...]
    output_dir: Path
    max_width: int
    max_fps: float
    colors: int
    webp_quality: int
    avif_crf: int
    speed: int
    target_bytes: int | None
    auto_optimize: bool
    make_preview: bool


@dataclass
class ConversionResult:
    fmt: str
    path: Path
    size: int
    params: EncodeParams


class CancelledError(RuntimeError):
    pass


def format_bytes(size: int) -> str:
    return f"{size / 1_000_000:.2f} MB"


def even_width(width: int) -> int:
    return max(32, int(width) // 2 * 2)


def format_fps(fps: float) -> str:
    return f"{fps:g}"


def executable(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"没有找到 {name}，请安装 FFmpeg 或把它加入 PATH。")
    return found


def supports_encoder(ffmpeg: str, name: str) -> bool:
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-h", f"encoder={name}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    return completed.returncode == 0


def run_command(
    command: list[str], cancel: threading.Event | None = None, cwd: Path | None = None
) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        cwd=str(cwd) if cwd else None,
    )
    while process.poll() is None:
        if cancel and cancel.wait(0.1):
            process.terminate()
            process.wait(timeout=5)
            raise CancelledError("操作已取消。")
    stderr = process.stderr.read() if process.stderr else ""
    if process.returncode != 0:
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else "未知错误"
        raise RuntimeError(f"FFmpeg 转换失败：{detail}")


def probe_video(source: Path) -> VideoInfo:
    command = [
        executable("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate:format=duration",
        "-of",
        "json",
        str(source),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("无法读取输入视频信息，请确认文件可以正常播放。")
    payload = json.loads(completed.stdout)
    stream = payload["streams"][0]
    numerator, denominator = stream.get("avg_frame_rate", "0/1").split("/")
    fps = float(numerator) / float(denominator) if float(denominator) else 0.0
    return VideoInfo(
        path=source,
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=fps or 30.0,
        duration=float(payload.get("format", {}).get("duration", 0.0)),
    )


class Converter:
    def __init__(
        self,
        source: Path,
        settings: ConversionSettings,
        log: Callable[[str], None],
        cancel: threading.Event | None = None,
    ) -> None:
        self.source = source
        self.settings = settings
        self.log = log
        self.cancel = cancel or threading.Event()
        self.info = probe_video(source)
        self.ffmpeg = executable("ffmpeg")
        self.img2webp = shutil.which("img2webp")
        self.has_svt_av1 = supports_encoder(self.ffmpeg, "libsvtav1")
        self.created_temp_files: list[Path] = []

    def use_gradient_safe_webp(self) -> bool:
        return bool(
            self.img2webp
            and self.settings.auto_optimize
            and self.settings.target_bytes
            and self.settings.target_bytes <= 20_000_000
        )

    def convert_all(self) -> tuple[list[ConversionResult], Path | None]:
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        self.log(
            f"输入：{self.info.path.name} | {self.info.width} x {self.info.height} | "
            f"{format_fps(self.info.fps)} fps | {self.info.duration:.2f} 秒"
        )
        results: list[ConversionResult] = []
        try:
            for fmt in self.settings.formats:
                if self.cancel.is_set():
                    raise CancelledError("操作已取消。")
                results.append(self.convert_format(fmt))
        finally:
            self.cleanup_temp_files()
        preview = create_preview_page(self.source, results) if self.settings.make_preview else None
        return results, preview

    def initial_params(self, fmt: str) -> EncodeParams:
        width_cap = self.settings.max_width or self.info.width
        fps_cap = self.settings.max_fps or self.info.fps
        width = min(width_cap, self.info.width)
        fps = min(fps_cap, self.info.fps)
        if self.settings.auto_optimize and self.settings.target_bytes:
            target_mb = self.settings.target_bytes / 1_000_000
            if fmt in {"gif", "apng"} and target_mb <= 20:
                width = min(width, 560)
                fps = min(fps, 10)
            elif fmt == "webp" and target_mb <= 20:
                if target_mb <= 10:
                    width = min(width, WEBP_AUTO_START_WIDTH)
                    fps = min(fps, WEBP_AUTO_START_FPS)
                elif self.use_gradient_safe_webp():
                    width = min(width, 1000)
                    fps = min(fps, 20)
                else:
                    width = min(width, 1200)
                    fps = min(fps, 24)
            elif fmt == "avif":
                width = min(width, AVIF_AUTO_MAX_WIDTH)
                fps = min(fps, AVIF_AUTO_MAX_FPS)
                if target_mb <= 20:
                    width = min(width, AVIF_AUTO_START_WIDTH)
                    fps = min(fps, AVIF_AUTO_START_FPS)
        return EncodeParams(
            width=even_width(width),
            fps=max(1.0, fps),
            colors=self.settings.colors,
            webp_quality=self.settings.webp_quality,
            avif_crf=self.settings.avif_crf,
            speed=self.settings.speed,
        )

    def convert_format(self, fmt: str) -> ConversionResult:
        label = FORMAT_LABELS[fmt]
        final_name = self.output_name(fmt)
        self.log(f"\n[{label}] 开始转换")
        params = self.initial_params(fmt)
        if self.settings.target_bytes and self.settings.auto_optimize:
            if fmt == "webp" and self.use_gradient_safe_webp():
                result = self.optimize_gradient_safe_webp(params, final_name)
            else:
                result = self.optimize_to_size(fmt, params, final_name)
        else:
            path = self.attempt_path(fmt, 1)
            self.encode(fmt, params, path)
            result = ConversionResult(fmt, path, path.stat().st_size, params)
            if self.settings.target_bytes and result.size > self.settings.target_bytes:
                raise RuntimeError(
                    f"{label} 输出为 {format_bytes(result.size)}，超过大小限制；"
                    "请勾选自动寻找最佳参数。"
                )
            self.commit_result(result, final_name)
            result.path = final_name
        self.log(
            f"[{label}] 完成：{result.path.name} | {format_bytes(result.size)} | "
            f"{result.params.width} px | {format_fps(result.params.fps)} fps"
        )
        return result

    def output_name(self, fmt: str) -> Path:
        suffix = ""
        if self.settings.target_bytes:
            suffix = f"_{self.settings.target_bytes / 1_000_000:g}MB_best"
        return self.settings.output_dir / f"{self.source.stem}{suffix}.{fmt}"

    def attempt_path(self, fmt: str, number: int) -> Path:
        path = self.settings.output_dir / f".{self.source.stem}.{fmt}.attempt_{number}.{fmt}"
        self.created_temp_files.append(path)
        return path

    def cleanup_temp_files(self) -> None:
        for path in self.created_temp_files:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    def encode(self, fmt: str, params: EncodeParams, destination: Path) -> None:
        if self.cancel.is_set():
            raise CancelledError("操作已取消。")
        if destination.exists():
            destination.unlink()
        common = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.source)]
        fps = format_fps(params.fps)
        scale_filter = f"fps={fps},scale={params.width}:-2:flags=lanczos"
        if fmt in {"gif", "apng"}:
            graph = (
                f"[0:v]{scale_filter},split[a][b];"
                f"[a]palettegen=stats_mode=diff:max_colors={params.colors}[p];"
                "[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle"
            )
            command = common + ["-filter_complex", graph, "-an"]
            if fmt == "gif":
                command += ["-loop", "0", str(destination)]
            else:
                command += ["-c:v", "apng", "-plays", "0", str(destination)]
        elif fmt == "webp":
            if self.use_gradient_safe_webp():
                self.encode_gradient_safe_webp(params, destination)
                return
            compression = max(0, min(6, round(6 - params.speed * 6 / 8)))
            command = common + [
                "-vf",
                scale_filter,
                "-an",
                "-c:v",
                "libwebp_anim",
                "-preset",
                "picture",
                "-q:v",
                str(params.webp_quality),
                "-compression_level",
                str(compression),
                "-loop",
                "0",
                str(destination),
            ]
        elif fmt == "avif":
            if self.settings.auto_optimize and self.settings.target_bytes and self.has_svt_av1:
                svt_preset = max(4, min(13, params.speed + 5))
                command = common + [
                    "-vf",
                    scale_filter,
                    "-an",
                    "-c:v",
                    "libsvtav1",
                    "-preset",
                    str(svt_preset),
                    "-crf",
                    str(params.avif_crf),
                    "-b:v",
                    "0",
                    "-pix_fmt",
                    "yuv420p10le",
                    "-loop",
                    "0",
                    str(destination),
                ]
            else:
                cpu_used = max(0, min(8, params.speed))
                command = common + [
                    "-vf",
                    scale_filter,
                    "-an",
                    "-c:v",
                    "libaom-av1",
                    "-cpu-used",
                    str(cpu_used),
                    "-crf",
                    str(params.avif_crf),
                    "-b:v",
                    "0",
                    "-pix_fmt",
                    "yuv420p10le",
                    "-loop",
                    "0",
                    str(destination),
                ]
        else:
            raise ValueError(fmt)
        run_command(command, self.cancel)

    def encode_gradient_safe_webp(self, params: EncodeParams, destination: Path) -> None:
        assert self.img2webp is not None
        fps = format_fps(params.fps)
        frame_dir = self.settings.output_dir / (
            f".{self.source.stem}.webp_frames_{params.width}_{fps.replace('.', '_')}"
        )
        shutil.rmtree(frame_dir, ignore_errors=True)
        frame_dir.mkdir(parents=True, exist_ok=True)
        try:
            scale_filter = f"fps={fps},scale={params.width}:-2:flags=lanczos"
            run_command(
                [
                    self.ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(self.source),
                    "-vf",
                    scale_filter,
                    "-an",
                    str(frame_dir / "frame_%04d.png"),
                ],
                self.cancel,
            )
            frames = sorted(frame_dir.glob("frame_*.png"))
            if not frames:
                raise RuntimeError("WebP 渐变保护无法生成中间帧。")
            method = min(3, max(0, round(6 - params.speed * 6 / 8)))
            duration = max(1, round(1000 / params.fps))
            command = [
                self.img2webp,
                "-loop",
                "0",
                "-mixed",
                "-sharp_yuv",
                "-min_size",
                "-q",
                str(params.webp_quality),
                "-m",
                str(method),
                "-d",
                str(duration),
                *[frame.name for frame in frames],
                "-o",
                str(destination.resolve()),
            ]
            run_command(command, self.cancel, frame_dir)
        finally:
            shutil.rmtree(frame_dir, ignore_errors=True)

    def optimize_gradient_safe_webp(
        self, start: EncodeParams, final_name: Path
    ) -> ConversionResult:
        target = self.settings.target_bytes
        assert target is not None
        params = start
        self.log("[WebP] 启用渐变保护编码，优先避免暗部和渐变背景出现方块")
        for attempt in range(1, 4):
            path = self.attempt_path("webp", attempt)
            self.log(
                f"[WebP] 渐变保护试压 {attempt}：{params.width} px / "
                f"{format_fps(params.fps)} fps / 质量 {params.webp_quality}"
            )
            self.encode("webp", params, path)
            size = path.stat().st_size
            self.log(f"[WebP] 试压结果：{format_bytes(size)}")
            result = ConversionResult("webp", path, size, params)
            if size <= target:
                self.log("[WebP] 已保留体积余量，用于维持渐变平滑度")
                self.commit_result(result, final_name)
                result.path = final_name
                return result
            factor = min(0.92, max(0.60, math.sqrt(target / size) * 0.94))
            width = even_width(params.width * factor)
            fps = params.fps if factor >= 0.85 else max(8.0, round(params.fps * factor))
            params = replace(params, width=width, fps=fps)
        raise RuntimeError(
            f"WebP 渐变保护模式无法压到 {format_bytes(target)} 以内，"
            "请进一步降低最大宽度或帧率。"
        )

    def optimize_to_size(
        self, fmt: str, start: EncodeParams, final_name: Path
    ) -> ConversionResult:
        target = self.settings.target_bytes
        assert target is not None
        cap_width = min(self.settings.max_width or self.info.width, self.info.width)
        cap_fps = min(self.settings.max_fps or self.info.fps, self.info.fps)
        if fmt == "avif":
            cap_width = min(cap_width, AVIF_AUTO_MAX_WIDTH)
            cap_fps = min(cap_fps, AVIF_AUTO_MAX_FPS)
            encoder_note = "快速 SVT-AV1 编码" if self.has_svt_av1 else "兼容编码"
            self.log(
                f"[AVIF] 自动限容使用{encoder_note}，寻优上限为 "
                f"{cap_width} px / {format_fps(cap_fps)} fps"
            )
        elif fmt == "webp":
            quality_floor = max(0, self.settings.webp_quality - WEBP_AUTO_QUALITY_DROP)
            self.log(
                f"[WebP] 自动限容优先保留平滑细节，质量不低于 {quality_floor}"
            )
        params = start
        best: ConversionResult | None = None
        seen: set[tuple[int, float, int, int, int]] = set()
        for attempt in range(1, 11):
            key = params.key()
            if key in seen:
                break
            seen.add(key)
            path = self.attempt_path(fmt, attempt)
            self.log(
                f"[{FORMAT_LABELS[fmt]}] 试压 {attempt}：{params.width} px / "
                f"{format_fps(params.fps)} fps{self.quality_text(fmt, params)}"
            )
            self.encode(fmt, params, path)
            size = path.stat().st_size
            self.log(f"[{FORMAT_LABELS[fmt]}] 试压结果：{format_bytes(size)}")
            current = ConversionResult(fmt, path, size, params)
            if size <= target:
                if best is None or self.score(fmt, current.params) > self.score(fmt, best.params):
                    best = current
                upgraded = self.upgrade(fmt, params, cap_width, cap_fps, size, target)
                if upgraded.key() == params.key():
                    break
                params = upgraded
                continue
            if best:
                refined = self.refine_between(fmt, best.params, params, seen)
                if refined is None or refined.key() in seen:
                    break
                params = refined
                continue
            params = self.downgrade(fmt, params, size, target)
        if best is None:
            raise RuntimeError(
                f"{FORMAT_LABELS[fmt]} 在当前参数范围内无法压到 {format_bytes(target)} 以内，"
                "请提高压缩率预设或进一步降低最大宽度/帧率。"
            )
        self.commit_result(best, final_name)
        best.path = final_name
        return best

    @staticmethod
    def quality_text(fmt: str, params: EncodeParams) -> str:
        if fmt == "webp":
            return f" / 质量 {params.webp_quality}"
        if fmt == "avif":
            return f" / CRF {params.avif_crf}"
        return f" / {params.colors} 色"

    @staticmethod
    def score(fmt: str, params: EncodeParams) -> float:
        base = (params.width**2) * params.fps
        if fmt == "webp":
            return base * (0.35 + params.webp_quality / 100 * 0.65)
        if fmt == "avif":
            return base * (1.1 - params.avif_crf / 80)
        return base * (0.7 + params.colors / 256 * 0.3)

    @staticmethod
    def upgrade(
        fmt: str,
        params: EncodeParams,
        cap_width: int,
        cap_fps: float,
        size: int,
        target: int,
    ) -> EncodeParams:
        room = target / max(size, 1)
        if fmt == "webp" and params.webp_quality < 96:
            return replace(params, webp_quality=min(96, params.webp_quality + 6))
        if fmt == "avif" and params.avif_crf > 8:
            return replace(params, avif_crf=max(8, params.avif_crf - 3))
        if fmt in {"gif", "apng"} and params.colors < 256 and room > 1.05:
            return replace(params, colors=min(256, params.colors + 32))
        if params.width < cap_width and room > 1.02:
            factor = min(1.18, max(1.03, math.sqrt(room) * 0.97))
            return replace(params, width=min(even_width(params.width * factor), even_width(cap_width)))
        if params.fps < cap_fps and room > 1.12:
            return replace(params, fps=min(cap_fps, params.fps + 2))
        return params

    @staticmethod
    def refine_between(
        fmt: str,
        best: EncodeParams,
        over: EncodeParams,
        seen: set[tuple[int, float, int, int, int]],
    ) -> EncodeParams | None:
        if fmt == "webp" and over.webp_quality > best.webp_quality + 1:
            quality = (best.webp_quality + over.webp_quality) // 2
            candidate = replace(best, webp_quality=quality)
            if candidate.key() not in seen:
                return candidate
        if fmt == "avif" and over.avif_crf < best.avif_crf - 1:
            options = range(over.avif_crf + 1, best.avif_crf)
            for crf in options:
                candidate = replace(best, avif_crf=crf)
                if candidate.key() not in seen:
                    return candidate
        if over.width > best.width + 8:
            candidate = replace(best, width=even_width((best.width + over.width) / 2))
            if candidate.key() not in seen:
                return candidate
        if over.fps > best.fps + 0.9:
            candidate = replace(best, fps=(best.fps + over.fps) / 2)
            if candidate.key() not in seen:
                return candidate
        return None

    def downgrade(self, fmt: str, params: EncodeParams, size: int, target: int) -> EncodeParams:
        ratio = size / target
        webp_floor = max(0, self.settings.webp_quality - WEBP_AUTO_QUALITY_DROP)
        avif_ceiling = min(52, self.settings.avif_crf + 14)
        if fmt == "webp" and params.webp_quality > webp_floor and ratio < 2.2:
            decrement = max(5, min(18, int((ratio - 1) * 22) + 5))
            return replace(params, webp_quality=max(webp_floor, params.webp_quality - decrement))
        if fmt == "avif" and params.avif_crf < avif_ceiling and ratio < 2.2:
            increment = max(3, min(12, int((ratio - 1) * 14) + 3))
            return replace(params, avif_crf=min(avif_ceiling, params.avif_crf + increment))
        if fmt in {"gif", "apng"} and params.colors > 128 and ratio < 1.25:
            return replace(params, colors=max(128, params.colors - 32))
        factor = min(0.90, max(0.42, math.sqrt(target / max(size, 1)) * 0.96))
        width = even_width(params.width * factor)
        fps = params.fps
        if factor < 0.80:
            fps = max(3.0, round(params.fps * max(factor, 0.55)))
        elif ratio > 1.08:
            fps = max(3.0, params.fps - 1)
        replacements: dict[str, int | float] = {"width": width, "fps": fps}
        if fmt == "webp":
            replacements["webp_quality"] = self.settings.webp_quality
        elif fmt == "avif":
            replacements["avif_crf"] = self.settings.avif_crf
        return replace(params, **replacements)

    @staticmethod
    def commit_result(result: ConversionResult, final_name: Path) -> None:
        if final_name.exists():
            final_name.unlink()
        result.path.replace(final_name)


def create_preview_page(source: Path, results: Iterable[ConversionResult]) -> Path | None:
    results = list(results)
    if not results:
        return None
    preview = results[0].path.parent / f"{source.stem}_动图对比预览.html"
    cards = []
    for result in results:
        label = FORMAT_LABELS[result.fmt]
        note = (
            f"{format_bytes(result.size)} | {result.params.width} px | "
            f"{format_fps(result.params.fps)} fps"
        )
        cards.append(
            f"""<article><div class="title"><b>{label}</b><span>{html.escape(note)}</span></div>
            <div class="stage"><img src="{html.escape(result.path.name)}" alt="{label}"></div></article>"""
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(source.stem)} - 动图对比</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0f1217;color:#f2f4f8;font:14px "Microsoft YaHei UI","Segoe UI",sans-serif}}
header{{position:sticky;top:0;z-index:1;padding:18px 24px;background:#0f1217eb;border-bottom:1px solid #29303d}}
h1{{font-size:22px;margin:0 0 6px}}p{{margin:0;color:#aab3c2}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px;padding:18px}}
article{{background:#171c24;border:1px solid #2a3342;border-radius:12px;overflow:hidden}}.title{{display:flex;justify-content:space-between;padding:12px 14px;border-bottom:1px solid #2a3342}}
.title b{{font-size:18px}}.title span{{color:#aab3c2}}.stage{{height:min(72vh,760px);padding:10px;background:#080a0e;display:flex;justify-content:center}}
img{{width:100%;height:100%;object-fit:contain}}
</style></head><body><header><h1>动图转换效果对比</h1><p>输入文件：{html.escape(source.name)}；所有输出同步循环播放。</p></header>
<main>{''.join(cards)}</main></body></html>"""
    preview.write_text(document, encoding="utf-8")
    return preview


class ConverterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(APP_TITLE)
        root.geometry("940x820")
        root.minsize(850, 720)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.latest_preview: Path | None = None
        self.worker: threading.Thread | None = None

        self.source = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.target_mb = tk.StringVar(value="10")
        self.auto_optimize = tk.BooleanVar(value=True)
        self.make_preview = tk.BooleanVar(value=True)
        self.preset = tk.StringVar(value=PRESETS["low"]["label"])
        self.width = tk.StringVar()
        self.fps = tk.StringVar()
        self.colors = tk.StringVar()
        self.webp_quality = tk.StringVar()
        self.avif_crf = tk.StringVar()
        self.speed = tk.StringVar()
        self.format_vars = {fmt: tk.BooleanVar(value=True) for fmt in FORMAT_LABELS}
        self.build_ui()
        self.apply_preset()
        self.root.after(100, self.process_events)

    def build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=14)
        container.pack(fill="both", expand=True)
        file_box = ttk.LabelFrame(container, text="输入与输出", padding=10)
        file_box.pack(fill="x")
        ttk.Label(file_box, text="输入视频").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(file_box, textvariable=self.source).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(file_box, text="选择...", command=self.choose_source).grid(row=0, column=2, padx=(8, 0), pady=4)
        ttk.Label(file_box, text="输出文件夹").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(file_box, textvariable=self.output_dir).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(file_box, text="选择...", command=self.choose_output).grid(row=1, column=2, padx=(8, 0), pady=4)
        file_box.columnconfigure(1, weight=1)

        format_box = ttk.LabelFrame(container, text="输出格式", padding=10)
        format_box.pack(fill="x", pady=(12, 0))
        for column, (fmt, label) in enumerate(FORMAT_LABELS.items()):
            ttk.Checkbutton(format_box, text=label, variable=self.format_vars[fmt]).grid(
                row=0, column=column, padx=(0, 28), sticky="w"
            )
        ttk.Label(
            format_box,
            text="WebP 适合发布；AVIF 压缩率最高；GIF 兼容兜底；APNG 适合透明或无损需求。",
            foreground="#555555",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        limit_box = ttk.LabelFrame(container, text="预设与文件大小限制", padding=10)
        limit_box.pack(fill="x", pady=(12, 0))
        ttk.Label(limit_box, text="压缩率预设").grid(row=0, column=0, sticky="w")
        preset_values = [PRESETS[key]["label"] for key in ("low", "medium", "high")]
        preset_select = ttk.Combobox(
            limit_box, textvariable=self.preset, values=preset_values, state="readonly", width=23
        )
        preset_select.grid(row=0, column=1, sticky="w", padx=(8, 24))
        preset_select.bind("<<ComboboxSelected>>", lambda _event: self.apply_preset())
        ttk.Label(limit_box, text="最大文件大小").grid(row=0, column=2, sticky="w")
        ttk.Entry(limit_box, textvariable=self.target_mb, width=10).grid(row=0, column=3, padx=(8, 4))
        ttk.Label(limit_box, text="MB（留空=不限制）").grid(row=0, column=4, sticky="w")
        ttk.Checkbutton(
            limit_box,
            text="超过限制时自动寻找满足上限的最佳参数",
            variable=self.auto_optimize,
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(10, 2))
        ttk.Label(
            limit_box,
            text="低压缩优先保留原尺寸/帧率；中、高压缩会限制可尝试的最大清晰度以提高处理速度。",
            foreground="#555555",
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))

        option_box = ttk.LabelFrame(container, text="影响画质和文件大小的选项", padding=10)
        option_box.pack(fill="x", pady=(12, 0))
        fields = [
            ("最大宽度", self.width, "px；0=保持源宽度。越大越清晰，体积增长最明显。"),
            ("最大帧率", self.fps, "fps；0=保持源帧率。越高越流畅，动态画面体积增长明显。"),
            ("GIF/APNG 色数", self.colors, "16-256；颜色越多，渐变越好，体积越大。"),
            ("WebP 质量", self.webp_quality, "0-100；数值越高细节越好，体积越大。"),
            ("AVIF CRF", self.avif_crf, "0-63；数值越低画质越高，体积越大。"),
            ("编码速度", self.speed, "0-8；越低通常压缩效率更好，但转换更慢。"),
        ]
        for row, (label, variable, note) in enumerate(fields):
            ttk.Label(option_box, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(option_box, textvariable=variable, width=10).grid(
                row=row, column=1, sticky="w", padx=(10, 15), pady=3
            )
            ttk.Label(option_box, text=note, foreground="#555555").grid(row=row, column=2, sticky="w", pady=3)

        action_box = ttk.Frame(container)
        action_box.pack(fill="x", pady=(12, 8))
        ttk.Checkbutton(
            action_box, text="完成后生成网页对比预览", variable=self.make_preview
        ).pack(side="left")
        self.preview_button = ttk.Button(
            action_box, text="打开最近预览", command=self.open_preview, state="disabled"
        )
        self.preview_button.pack(side="right", padx=(8, 0))
        self.cancel_button = ttk.Button(action_box, text="取消", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="right", padx=(8, 0))
        self.start_button = ttk.Button(action_box, text="开始转换", command=self.start)
        self.start_button.pack(side="right")

        log_box = ttk.LabelFrame(container, text="处理日志", padding=8)
        log_box.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_box, height=12, wrap="word", state="disabled")
        scrollbar = ttk.Scrollbar(log_box, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def choose_source(self) -> None:
        value = filedialog.askopenfilename(title="选择视频", filetypes=VIDEO_TYPES)
        if value:
            self.source.set(value)
            if not self.output_dir.get():
                self.output_dir.set(str(Path(value).parent))

    def choose_output(self) -> None:
        value = filedialog.askdirectory(title="选择输出文件夹")
        if value:
            self.output_dir.set(value)

    def apply_preset(self) -> None:
        preset = PRESETS[PRESET_BY_LABEL[self.preset.get()]]
        self.width.set(str(preset["width"]))
        self.fps.set(str(preset["fps"]))
        self.colors.set(str(preset["colors"]))
        self.webp_quality.set(str(preset["webp_quality"]))
        self.avif_crf.set(str(preset["avif_crf"]))
        self.speed.set(str(preset["speed"]))

    def parse_settings(self) -> tuple[Path, ConversionSettings]:
        source = Path(self.source.get().strip())
        if not source.is_file():
            raise ValueError("请选择有效的输入视频文件。")
        formats = tuple(fmt for fmt, chosen in self.format_vars.items() if chosen.get())
        if not formats:
            raise ValueError("请至少选择一种输出格式。")
        output_dir = Path(self.output_dir.get().strip() or source.parent)
        target_text = self.target_mb.get().strip()
        target_bytes = None
        if target_text:
            target_value = float(target_text)
            if target_value <= 0:
                raise ValueError("最大文件大小必须大于 0。")
            target_bytes = int(target_value * 1_000_000)
        max_width = int(self.width.get())
        max_fps = float(self.fps.get())
        colors = int(self.colors.get())
        webp_quality = int(self.webp_quality.get())
        avif_crf = int(self.avif_crf.get())
        speed = int(self.speed.get())
        if max_width < 0 or max_fps < 0:
            raise ValueError("最大宽度和帧率不可小于 0。")
        if max_width and max_width < 32:
            raise ValueError("最大宽度至少为 32 px，或填写 0 保持源宽度。")
        if not 16 <= colors <= 256:
            raise ValueError("GIF/APNG 色数需在 16 到 256 之间。")
        if not 0 <= webp_quality <= 100:
            raise ValueError("WebP 质量需在 0 到 100 之间。")
        if not 0 <= avif_crf <= 63:
            raise ValueError("AVIF CRF 需在 0 到 63 之间。")
        if not 0 <= speed <= 8:
            raise ValueError("编码速度需在 0 到 8 之间。")
        return source, ConversionSettings(
            formats=formats,
            output_dir=output_dir,
            max_width=max_width,
            max_fps=max_fps,
            colors=colors,
            webp_quality=webp_quality,
            avif_crf=avif_crf,
            speed=speed,
            target_bytes=target_bytes,
            auto_optimize=self.auto_optimize.get(),
            make_preview=self.make_preview.get(),
        )

    def append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def start(self) -> None:
        try:
            source, settings = self.parse_settings()
        except (ValueError, OSError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.append_log("准备开始转换...")

        def work() -> None:
            try:
                converter = Converter(
                    source,
                    settings,
                    lambda text: self.events.put(("log", text)),
                    self.cancel_event,
                )
                results, preview = converter.convert_all()
                self.events.put(("done", (results, preview)))
            except CancelledError as exc:
                self.events.put(("cancelled", str(exc)))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.append_log("正在取消，请等待当前编码进程退出...")

    def open_preview(self) -> None:
        if self.latest_preview and self.latest_preview.exists():
            webbrowser.open(self.latest_preview.resolve().as_uri())

    def process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "log":
                    self.append_log(str(payload))
                elif event == "done":
                    results, preview = payload
                    self.finish_work()
                    self.latest_preview = preview
                    if preview:
                        self.preview_button.configure(state="normal")
                    summary = "\n".join(
                        f"{FORMAT_LABELS[item.fmt]}：{item.path.name}（{format_bytes(item.size)}）"
                        for item in results
                    )
                    self.append_log("\n全部处理完成。")
                    messagebox.showinfo(APP_TITLE, "转换完成：\n" + summary)
                    if preview:
                        webbrowser.open(preview.resolve().as_uri())
                elif event == "cancelled":
                    self.finish_work()
                    self.append_log(str(payload))
                elif event == "error":
                    self.finish_work()
                    self.append_log("错误：" + str(payload))
                    messagebox.showerror(APP_TITLE, str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def finish_work(self) -> None:
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")


def build_cli_settings(arguments: argparse.Namespace) -> ConversionSettings:
    preset = PRESETS[arguments.preset]
    target = int(arguments.target_mb * 1_000_000) if arguments.target_mb else None
    return ConversionSettings(
        formats=tuple(arguments.formats.split(",")),
        output_dir=Path(arguments.output_dir) if arguments.output_dir else Path(arguments.input).parent,
        max_width=arguments.width if arguments.width is not None else preset["width"],
        max_fps=arguments.fps if arguments.fps is not None else preset["fps"],
        colors=arguments.colors if arguments.colors is not None else preset["colors"],
        webp_quality=(
            arguments.webp_quality if arguments.webp_quality is not None else preset["webp_quality"]
        ),
        avif_crf=arguments.avif_crf if arguments.avif_crf is not None else preset["avif_crf"],
        speed=arguments.speed if arguments.speed is not None else preset["speed"],
        target_bytes=target,
        auto_optimize=not arguments.no_optimize,
        make_preview=not arguments.no_preview,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="将视频转换为 GIF、WebP、APNG 或 AVIF 动图。")
    parser.add_argument("--input", help="输入视频路径；不传入时启动图形界面。")
    parser.add_argument("--formats", default="gif,webp,apng,avif", help="输出格式，以逗号分隔。")
    parser.add_argument("--output-dir", help="输出目录。")
    parser.add_argument("--preset", choices=tuple(PRESETS), default="low", help="压缩率预设。")
    parser.add_argument("--target-mb", type=float, help="最大输出大小（十进制 MB）。")
    parser.add_argument("--width", type=int, help="最大宽度；0 表示源宽度。")
    parser.add_argument("--fps", type=float, help="最大帧率；0 表示源帧率。")
    parser.add_argument("--colors", type=int, help="GIF/APNG 色数。")
    parser.add_argument("--webp-quality", type=int, help="WebP 质量 0-100。")
    parser.add_argument("--avif-crf", type=int, help="AVIF CRF 0-63，越低质量越高。")
    parser.add_argument("--speed", type=int, help="编码速度 0-8，越低通常压缩效率越好。")
    parser.add_argument("--no-optimize", action="store_true", help="大小超限时不自动优化。")
    parser.add_argument("--no-preview", action="store_true", help="不生成对比预览页。")
    arguments = parser.parse_args()
    if not arguments.input:
        load_tkinter()
        root = tk.Tk()
        ttk.Style(root).theme_use("vista" if "vista" in ttk.Style(root).theme_names() else "clam")
        ConverterApp(root)
        root.mainloop()
        return 0
    source = Path(arguments.input)
    if not source.exists():
        parser.error("输入文件不存在。")
    invalid = set(arguments.formats.split(",")) - set(FORMAT_LABELS)
    if invalid:
        parser.error("不支持的格式：" + ", ".join(sorted(invalid)))
    settings = build_cli_settings(arguments)
    converter = Converter(source, settings, print)
    results, preview = converter.convert_all()
    for result in results:
        print(f"{FORMAT_LABELS[result.fmt]} -> {result.path} ({format_bytes(result.size)})")
    if preview:
        print(f"预览页 -> {preview}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
