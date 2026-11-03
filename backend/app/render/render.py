"""Rendu final en une seule passe FFmpeg : coupes, recadrage, zooms, sous-titres, musique, volume."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..media import ffmpeg
from ..models import Project, Timeline, Transcript
from . import captions as cap

FPS = 30
SIZES = {
    ("16:9", "1080p"): (1920, 1080), ("16:9", "720p"): (1280, 720),
    ("9:16", "1080p"): (1080, 1920), ("9:16", "720p"): (720, 1280),
    ("1:1", "1080p"): (1080, 1080), ("1:1", "720p"): (720, 720),
}


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def build_command(project: Project, timeline: Timeline, transcripts: dict[str, Transcript],
                  out_path: Path, work_dir: Path, quality: str = "1080p",
                  music_path: str | None = None, use_proxy: bool = False) -> tuple[list[str], float]:
    if not timeline.clips:
        raise ValueError("La timeline est vide : rien à exporter.")
    W, H = SIZES[(timeline.format, quality)]
    inputs: list[str] = []
    filters: list[str] = []
    n_in = 0
    concat_pads = []
    total = 0.0

    for i, c in enumerate(timeline.clips):
        m = project.media_by_id(c.media_id)
        src = m.proxy if (use_proxy and m.proxy) else m.path
        d = c.duration
        total += d
        if m.has_video:
            inputs += ["-ss", f"{c.src_in:.3f}", "-t", f"{d:.3f}", "-i", src]
            vin = n_in
            n_in += 1
            zw, zh = _even(W * c.zoom), _even(H * c.zoom)
            filters.append(
                f"[{vin}:v:0]setpts=PTS-STARTPTS,fps={FPS},"
                f"scale={zw}:{zh}:force_original_aspect_ratio=increase:flags=bicubic,"
                f"crop={W}:{H},setsar=1,format=yuv420p,trim=duration={d:.3f}[v{i}]"
            )
        else:
            inputs += ["-f", "lavfi", "-t", f"{d:.3f}", "-i", f"color=c=black:s={W}x{H}:r={FPS}"]
            vin = n_in
            n_in += 1
            filters.append(f"[{vin}:v]setsar=1,format=yuv420p[v{i}]")
        if m.has_audio:
            if not m.has_video:
                inputs += ["-ss", f"{c.src_in:.3f}", "-t", f"{d:.3f}", "-i", src]
                ain = n_in
                n_in += 1
            else:
                ain = vin
            fade_out = max(0.0, d - 0.03)
            filters.append(
                f"[{ain}:a:0]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                f"volume={c.volume:.2f},apad,atrim=duration={d:.3f},"
                f"afade=t=in:d=0.02,afade=t=out:st={fade_out:.3f}:d=0.03[a{i}]"
            )
        else:
            inputs += ["-f", "lavfi", "-t", f"{d:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            filters.append(f"[{n_in}:a]anull[a{i}]")
            n_in += 1
        concat_pads.append(f"[v{i}][a{i}]")

    filters.append(f"{''.join(concat_pads)}concat=n={len(timeline.clips)}:v=1:a=1[vc][ac]")

    vlabel = "vc"
    if timeline.captions != "aucun":
        lines = cap.build_lines(timeline, transcripts)
        if lines:
            ass = cap.to_ass(lines, W, H, timeline.captions, timeline.format)
            (work_dir / "captions.ass").write_text(ass, encoding="utf-8")
            filters.append("[vc]ass=captions.ass[vout]")
            vlabel = "vout"

    alabel = "ac"
    if music_path:
        inputs += ["-stream_loop", "-1", "-i", music_path]
        mi = n_in
        n_in += 1
        vol = timeline.music.volume
        fade = min(2.0, total / 4)
        filters.append(
            f"[{mi}:a:0]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={total:.3f},"
            f"volume={vol:.3f},afade=t=in:d=0.5,afade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f}[mus]"
        )
        if timeline.music.ducking:
            filters.append("[ac]asplit=2[voice][sc]")
            filters.append("[mus][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[duck]")
            filters.append("[voice][duck]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            filters.append("[ac][mus]amix=inputs=2:duration=first:normalize=0[mix]")
        alabel = "mix"
    filters.append(f"[{alabel}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")

    script = work_dir / "filtergraph.txt"
    script.write_text(";\n".join(filters), encoding="utf-8")

    enc, enc_opts = ffmpeg.best_h264_encoder()
    args = [
        *inputs,
        "-filter_complex_script", str(script),
        "-map", f"[{vlabel}]", "-map", "[aout]",
        "-c:v", enc, *enc_opts, "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(out_path),
    ]
    return args, total


def render(project: Project, timeline: Timeline, transcripts: dict[str, Transcript], out_path: Path,
           work_dir: Path, quality: str = "1080p", music_path: str | None = None,
           on_progress: Callable[[float], None] | None = None) -> Path:
    args, total = build_command(project, timeline, transcripts, out_path, work_dir, quality, music_path)
    # Le filtre ass lit captions.ass en chemin relatif depuis work_dir (évite l'échappement des chemins).
    ffmpeg.run(args, total, on_progress, cwd=work_dir)
    srt = cap.to_srt(cap.build_lines(timeline, transcripts))
    out_path.with_suffix(".srt").write_text(srt, encoding="utf-8")
    return out_path
