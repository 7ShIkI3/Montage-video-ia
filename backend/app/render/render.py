"""Rendu final en une seule passe FFmpeg : coupes, vitesse, effets, transitions, B-roll, textes,
sous-titres, bruitages, musique et normalisation du volume."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .. import storage
from ..analysis import faces as faces_an
from ..media import ffmpeg
from ..models import Project, Timeline, Transcript
from . import captions as cap
from . import effects as fx

FPS = 30  # cadence par défaut
FPS_CHOICES = (24, 25, 30, 50, 60, 100, 120)
QUALITIES = {"720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160}  # côté court de l'image
SIZES = {}
for _q, _s in QUALITIES.items():
    _l = _s * 16 // 9
    SIZES[("16:9", _q)] = (_l, _s)
    SIZES[("9:16", _q)] = (_s, _l)
    SIZES[("1:1", _q)] = (_s, _s)


def source_fps(project: Project, timeline: Timeline) -> int:
    """Cadence « comme la source » : la plus haute des rushes utilisés, ramenée au choix standard le plus proche."""
    used = {c.media_id for c in timeline.clips}
    best = max((m.fps for m in project.media if m.id in used and m.has_video and m.fps), default=FPS)
    return min(FPS_CHOICES, key=lambda f: (abs(f - best), -f))


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _cover(W: int, H: int, zoom: float = 1.0) -> str:
    zw, zh = _even(W * zoom), _even(H * zoom)
    return f"scale={zw}:{zh}:force_original_aspect_ratio=increase:flags=bicubic,crop={W}:{H}"


def _cover_tracked(W: int, H: int, zoom: float, kf: list[tuple[float, float]]) -> str:
    """Comme _cover, mais le cadre suit le visage (x interpolé dans le temps)."""
    zw, zh = _even(W * zoom), _even(H * zoom)
    cx = faces_an.ffmpeg_expr(kf)
    return (f"scale={zw}:{zh}:force_original_aspect_ratio=increase:flags=bicubic,"
            f"crop={W}:{H}:x='clip(({cx})*iw-{W}/2,0,iw-{W})':y='(ih-{H})/2'")


def _cover_focus(W: int, H: int, zoom: float, fx_: float, fy: float) -> str:
    """Cadre centré sur un point choisi (zone sélectionnée dans le lecteur)."""
    zw, zh = _even(W * zoom), _even(H * zoom)
    return (f"scale={zw}:{zh}:force_original_aspect_ratio=increase:flags=bicubic,"
            f"crop={W}:{H}:x='clip({fx_:.4f}*iw-{W}/2,0,iw-{W})':y='clip({fy:.4f}*ih-{H}/2,0,ih-{H})'")


def mask_filters(masks, tag: str, face_kf: list[tuple[float, float]] | None = None) -> list[str]:
    """Flou, pixelisation, cache ou cadre sur des zones de l'image source (avant recadrage).
    Une zone posée sur un visage le suit dans le temps (face_kf : trajectoire du visage dans le plan)."""
    out: list[str] = []
    for k, mk in enumerate(masks):
        r = mk.clamp()
        fx_ = f"{r.x:.4f}"
        if mk.follow_dx is not None and face_kf:
            fx_ = f"clip(({faces_an.ffmpeg_expr(face_kf)})+({mk.follow_dx:.4f}),0,{1 - r.w:.4f})"
        x, y, w, h = (f"'iw*{fx_}'", f"ih*{r.y:.4f}", f"iw*{r.w:.4f}", f"ih*{r.h:.4f}")
        if mk.kind == "noir":
            out.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=black:t=fill")
        elif mk.kind == "cadre":
            out.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0xFFE500@0.95:t='max(4,ih/120)'")
        else:
            a, b, bb = f"{tag}m{k}a", f"{tag}m{k}b", f"{tag}m{k}c"
            if mk.kind == "pixel":
                proc = "scale='max(2,iw/18)':'max(2,ih/18)':flags=area,scale='iw*18':'ih*18':flags=neighbor"
            else:
                proc = "boxblur=luma_radius='min(w,h)/6':luma_power=3:chroma_radius='min(cw,ch)/6':chroma_power=3"
            out.append(f"split[{a}][{b}];[{b}]crop=w={w}:h={h}:x={x}:y={y}:exact=1,{proc}[{bb}];"
                       f"[{a}][{bb}]overlay=x='main_w*{fx_}':y=main_h*{r.y:.4f}")
    return out


def build_command(project: Project, timeline: Timeline, transcripts: dict[str, Transcript],
                  out_path: Path, work_dir: Path, quality: str = "1080p",
                  music_path: str | None = None, use_proxy: bool = False,
                  fps: int | None = None, smooth: bool = False) -> tuple[list[str], float]:
    if not timeline.clips:
        raise ValueError("La timeline est vide : rien à exporter.")
    W, H = SIZES[(timeline.format, quality)]
    FPS = fps or source_fps(project, timeline)
    inputs: list[str] = []
    filters: list[str] = []
    n_in = 0
    concat_pads = []
    total = 0.0
    clips = timeline.clips

    for i, c in enumerate(clips):
        m = project.media_by_id(c.media_id)
        src = m.proxy if (use_proxy and m.proxy) else m.path
        d = c.duration  # durée timeline
        sd = c.source_duration
        speed = max(0.25, min(4.0, c.speed))
        total += d
        nxt = clips[i + 1] if i + 1 < len(clips) else None
        out_color = fx.fades_out_before(nxt.transition) if nxt else None
        td_out = nxt.transition_duration if nxt else 0.0

        # --- vidéo ---
        vchain: list[str] = []
        if m.has_video:
            inputs += ["-ss", f"{c.src_in:.3f}", "-t", f"{sd:.3f}", "-i", src]
            vin = n_in
            n_in += 1
            src_rate = (m.fps or FPS) * speed  # images source disponibles par seconde de timeline
            if smooth and src_rate < FPS - 1:
                # Interpolation de mouvement : crée les images manquantes (60-120 i/s, ralentis fluides).
                vchain.append(f"[{vin}:v:0]setpts=(PTS-STARTPTS)/{speed:.4f},"
                              f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
            else:
                vchain.append(f"[{vin}:v:0]setpts=(PTS-STARTPTS)/{speed:.4f},fps={FPS}")
            face_kf = None
            if any(mk.follow_dx is not None for mk in c.masks):
                face_kf = faces_an.keyframes((storage.load_signals(project.id, m.id) or {}).get("faces"),
                                             c.src_in, c.src_out, speed)
            vchain += mask_filters(c.masks, f"c{i}", face_kf)
            kf: list[tuple[float, float]] = []
            if c.focus_x is not None:
                vchain.append(_cover_focus(W, H, c.zoom, c.focus_x, c.focus_y if c.focus_y is not None else 0.5))
            else:
                if timeline.reframe and m.width / max(1, m.height) > W / H + 0.01:
                    sig = storage.load_signals(project.id, m.id) or {}
                    kf = faces_an.keyframes(sig.get("faces"), c.src_in, c.src_out, speed)
                vchain.append(_cover_tracked(W, H, c.zoom, kf) if kf else _cover(W, H, c.zoom))
        else:
            inputs += ["-f", "lavfi", "-t", f"{d:.3f}", "-i", f"color=c=black:s={W}x{H}:r={FPS}"]
            vin = n_in
            n_in += 1
            vchain.append(f"[{vin}:v]null")
        vchain.append("setsar=1")
        for e in c.effects:
            f = fx.video_effect_filter(e, W, H, d, FPS)
            if f:
                vchain.append(f)
        vchain += fx.transition_filters(c.transition, c.transition_duration, W, H, FPS)
        if out_color:
            vchain.append(f"fade=t=out:st={max(0.0, d - td_out):.3f}:d={td_out:.2f}:color={out_color}")
        vchain.append(f"setsar=1,format=yuv420p,trim=duration={d:.3f},setpts=PTS-STARTPTS[v{i}]")
        filters.append(",".join(vchain))

        # --- audio ---
        if m.has_audio:
            if not m.has_video:
                inputs += ["-ss", f"{c.src_in:.3f}", "-t", f"{sd:.3f}", "-i", src]
                ain = n_in
                n_in += 1
            else:
                ain = vin
            achain = [f"[{ain}:a:0]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo"]
            if abs(speed - 1.0) > 1e-3:
                achain.append(fx.atempo_chain(speed))
            for e in c.audio_effects:
                spec = fx.AUDIO_EFFECTS.get(e)
                if spec:
                    achain.append(spec["filter"])
            fin = c.transition_duration if c.transition in ("fondu_noir", "fondu_blanc") else 0.02
            fout = td_out if out_color else 0.03
            achain.append(f"volume={c.volume:.2f},apad,atrim=duration={d:.3f}")
            achain.append(f"afade=t=in:d={fin:.2f},afade=t=out:st={max(0.0, d - fout):.3f}:d={fout:.2f}[a{i}]")
            filters.append(",".join(achain))
        else:
            inputs += ["-f", "lavfi", "-t", f"{d:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            filters.append(f"[{n_in}:a]anull[a{i}]")
            n_in += 1
        concat_pads.append(f"[v{i}][a{i}]")

    filters.append(f"{''.join(concat_pads)}concat=n={len(clips)}:v=1:a=1[vc][ac]")
    vlabel = "vc"

    # --- piste vidéo 2 : plans de coupe (B-roll) ---
    for j, o in enumerate(timeline.overlays):
        if o.start >= total or o.duration <= 0.05:
            continue
        try:
            m = project.media_by_id(o.media_id)
        except KeyError:
            continue
        if not m.has_video:
            continue
        od = min(o.duration, total - o.start)
        inputs += ["-ss", f"{o.src_in:.3f}", "-t", f"{od:.3f}", "-i", m.path]
        oi = n_in
        n_in += 1
        if o.mode == "incrustation":
            pw = _even(W * 0.34)
            ph = _even(pw * H / W)
            prep = (f"scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph},"
                    f"pad={pw + 8}:{ph + 8}:4:4:color=white")
            x, y = f"W-w-{int(W * 0.04)}", f"{int(H * 0.05)}"
        else:
            prep = _cover(W, H)
            x, y = "0", "0"
        filters.append(
            f"[{oi}:v:0]setpts=PTS-STARTPTS,fps={FPS},{prep},setsar=1,format=yuva420p,"
            f"fade=t=in:st=0:d=0.2:alpha=1,fade=t=out:st={max(0.0, od - 0.2):.3f}:d=0.2:alpha=1,"
            f"setpts=PTS+{o.start:.3f}/TB[ov{j}]"
        )
        filters.append(f"[{vlabel}][ov{j}]overlay=x={x}:y={y}:eof_action=pass:"
                       f"enable='between(t,{o.start:.3f},{o.start + od:.3f})'[vo{j}]")
        vlabel = f"vo{j}"

    # --- étalonnage global ---
    if timeline.look and timeline.look != "aucun":
        f = fx.video_effect_filter(timeline.look, W, H, total, FPS)
        if f:
            filters.append(f"[{vlabel}]{f}[vlook]")
            vlabel = "vlook"

    # --- sous-titres + piste Textes (un seul fichier ASS) ---
    lines = cap.build_lines(timeline, transcripts) if timeline.captions != "aucun" else []
    if lines or timeline.texts:
        ass = cap.to_ass(lines, W, H, timeline.captions, timeline.format, timeline.texts)
        (work_dir / "captions.ass").write_text(ass, encoding="utf-8")
        filters.append(f"[{vlabel}]ass=captions.ass[vout]")
        vlabel = "vout"

    # --- piste Effets sonores ---
    alabel = "ac"
    sfx_files = fx.ensure_sfx() if timeline.sfx else {}
    sfx_labels = []
    for k, s in enumerate(timeline.sfx):
        path = sfx_files.get(s.kind)
        if not path or s.start >= total:
            continue
        inputs += ["-i", str(path)]
        si = n_in
        n_in += 1
        ms = int(s.start * 1000)
        filters.append(f"[{si}:a]aresample=48000,aformat=channel_layouts=stereo,volume={s.volume:.2f},"
                       f"adelay={ms}|{ms}[sx{k}]")
        sfx_labels.append(f"[sx{k}]")
    if sfx_labels:
        filters.append(f"[ac]{''.join(sfx_labels)}amix=inputs={len(sfx_labels) + 1}:duration=first:normalize=0[afx]")
        alabel = "afx"

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
            filters.append(f"[{alabel}]asplit=2[voice][sc]")
            filters.append("[mus][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[duck]")
            filters.append("[voice][duck]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            filters.append(f"[{alabel}][mus]amix=inputs=2:duration=first:normalize=0[mix]")
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
           on_progress: Callable[[float], None] | None = None, fps: int | None = None,
           smooth: bool = False) -> Path:
    args, total = build_command(project, timeline, transcripts, out_path, work_dir, quality, music_path,
                                fps=fps, smooth=smooth)
    # Le filtre ass lit captions.ass en chemin relatif depuis work_dir (évite l'échappement des chemins).
    ffmpeg.run(args, total, on_progress, cwd=work_dir)
    srt = cap.to_srt(cap.build_lines(timeline, transcripts))
    out_path.with_suffix(".srt").write_text(srt, encoding="utf-8")
    return out_path
