"""Enveloppe fine autour de FFmpeg / FFprobe."""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from .. import config
from ..jobs import JobCancelled

FFMPEG = os.environ.get("MONTEUR_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("MONTEUR_FFPROBE") or shutil.which("ffprobe") or "ffprobe"

ProgressCb = Callable[[float], None]


class FFmpegError(RuntimeError):
    pass


def probe(path: str | Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise FFmpegError(f"Fichier illisible : {out.stderr.strip()[:300]}")
    data = json.loads(out.stdout)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0)
    fps = 30.0
    width = height = 0
    if v:
        width, height = int(v.get("width", 0)), int(v.get("height", 0))
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = abs(int(sd["rotation"]))
        if rot in (90, 270):
            width, height = height, width
        num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
        try:
            fps = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            fps = 30.0
        if not 1 <= fps <= 240:
            fps = 30.0
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "has_video": v is not None,
        "has_audio": a is not None,
    }


_TIME_RE = re.compile(r"out_time_us=(\d+)")


def run(args: list[str], duration: float | None = None, on_progress: ProgressCb | None = None,
        cwd: str | Path | None = None, cancel: threading.Event | None = None) -> None:
    """Lance ffmpeg avec suivi de progression (0..1) via -progress. `cancel` posé : ffmpeg est tué (JobCancelled)."""
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd)
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                raise JobCancelled()
            if on_progress and duration:
                m = _TIME_RE.match(line.strip())
                if m:
                    on_progress(min(1.0, int(m.group(1)) / 1e6 / duration))
    except BaseException:  # annulation, ou erreur dans le callback : on ne laisse pas ffmpeg tourner seul
        proc.kill()
        proc.wait()
        raise
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise FFmpegError(stderr.strip()[-1500:] or "ffmpeg a échoué")


def make_proxy(src: str | Path, dst: str | Path, duration: float, on_progress: ProgressCb | None = None,
               cancel: threading.Event | None = None) -> None:
    """Proxy léger pour la lecture : 540p, GOP court pour un seek rapide."""
    h = config.PROXY_HEIGHT
    run([
        "-i", str(src),
        "-map", "0:v:0?", "-map", "0:a:0?",
        "-vf", f"scale=-2:'min({h},ih)':flags=fast_bilinear,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-g", "15", "-bf", "0",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-movflags", "+faststart", str(dst),
    ], duration, on_progress, cancel=cancel)


def decode_audio(src: str | Path, sr: int = config.AUDIO_SR) -> np.ndarray:
    """Audio mono float32 à `sr` Hz (utilisé par l'analyse et Whisper)."""
    proc = subprocess.run(
        [FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1",
         "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.decode(errors="ignore")[-500:])
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def decode_gray_frames(src: str | Path, fps: float = config.ANALYSIS_FPS, w: int = 64, h: int = 36) -> np.ndarray:
    """Images minuscules en niveaux de gris pour mesurer mouvement et changements de plan."""
    proc = subprocess.run(
        [FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(src), "-an",
         "-vf", f"fps={fps},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.decode(errors="ignore")[-500:])
    frames = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = frames.size // (w * h)
    return frames[: n * w * h].reshape(n, h, w).astype(np.float32)


def decode_rgb_frames(src: str | Path, fps: float = config.ANALYSIS_FPS, w: int = 32, h: int = 18) -> np.ndarray:
    """Images minuscules en couleur (n, h, w, 3) pour les teintes dominantes (écran rouge quand on encaisse)."""
    proc = subprocess.run(
        [FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(src), "-an",
         "-vf", f"fps={fps},scale={w}:{h}:flags=area,format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.decode(errors="ignore")[-500:])
    frames = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = frames.size // (w * h * 3)
    return frames[: n * w * h * 3].reshape(n, h, w, 3)


def decode_gray_small(src: str | Path, fps: float, width: int = 480) -> np.ndarray:
    """Images en niveaux de gris de largeur fixe (hauteur proportionnelle), pour la détection de visages."""
    info = probe(src)
    h = max(2, int(round(width * info["height"] / max(1, info["width"]) / 2)) * 2)
    proc = subprocess.run(
        [FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(src), "-an",
         "-vf", f"fps={fps},scale={width}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.decode(errors="ignore")[-500:])
    frames = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = frames.size // (width * h)
    return frames[: n * width * h].reshape(n, h, width)


def thumbnail(src: str | Path, dst: str | Path, at: float) -> None:
    run(["-ss", f"{max(0.0, at):.3f}", "-i", str(src), "-frames:v", "1",
         "-vf", "scale=320:-2", "-q:v", "4", str(dst)])


@functools.lru_cache(maxsize=1)
def best_h264_encoder() -> tuple[str, list[str]]:
    """Choisit le meilleur encodeur H.264 réellement utilisable sur la machine."""
    candidates = [
        ("h264_nvenc", ["-preset", "p5", "-cq", "21", "-b:v", "0"]),
        ("h264_videotoolbox", ["-q:v", "60"]),
        ("h264_qsv", ["-global_quality", "21"]),
        ("h264_amf", ["-quality", "quality", "-rc", "cqp", "-qp_i", "21", "-qp_p", "21"]),
    ]
    for name, opts in candidates:
        test = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=black:s=256x256:d=0.2", "-c:v", name, *opts, "-f", "null", "-"],
            capture_output=True,
        )
        if test.returncode == 0:
            return name, opts
    return "libx264", ["-preset", "veryfast", "-crf", "20"]
