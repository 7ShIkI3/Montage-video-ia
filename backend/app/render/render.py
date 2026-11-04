"""Rendu final en une seule passe FFmpeg : coupes, vitesse, effets, transitions, B-roll, textes,
sous-titres, bruitages, musique et normalisation du volume."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import numpy as np

from .. import storage
from ..analysis import faces as faces_an
from ..media import ffmpeg
from ..models import Project, Timeline, Transcript
from . import captions as cap
from . import effects as fx
from . import luts as luts_mod
from . import scene3d

FPS = 30  # cadence par défaut
FPS_CHOICES = (24, 25, 30, 50, 60, 100, 120)
QUALITIES = {"720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160}  # côté court de l'image
SIZES = {}
for _q, _s in QUALITIES.items():
    _l = _s * 16 // 9
    SIZES[("16:9", _q)] = (_l, _s)
    SIZES[("9:16", _q)] = (_s, _l)
    SIZES[("1:1", _q)] = (_s, _s)


# Conteneurs de sortie : (extension, options vidéo, options audio). "mp4" utilise le meilleur encodeur H.264.
CONTAINERS = {
    "mp4": ("mp4", None, ["-c:a", "aac", "-b:a", "192k"]),
    "webm": ("webm", ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
                      "-crf", "31", "-b:v", "0", "-pix_fmt", "yuv420p"], ["-c:a", "libopus", "-b:a", "160k"]),
    "mov": ("mov", ["-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le"], ["-c:a", "pcm_s16le"]),
}


def video_codec_args(container: str = "mp4") -> list[str]:
    _, v, _ = CONTAINERS.get(container, CONTAINERS["mp4"])
    if v is None:
        enc, opts = ffmpeg.best_h264_encoder()
        return ["-c:v", enc, *opts, "-pix_fmt", "yuv420p"]
    return list(v)


def audio_codec_args(container: str = "mp4") -> list[str]:
    return list(CONTAINERS.get(container, CONTAINERS["mp4"])[2])


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


def kf_expr(kfs, key: str, tvar: str = "t") -> str:
    """Valeur interpolée d'une image clé (zoom, x ou y) en expression FFmpeg du temps du plan."""
    pts = sorted(kfs, key=lambda k: k.t)
    vals = [(k.t, float(getattr(k, key)), k.ease) for k in pts]
    expr = f"{vals[-1][1]:.5f}"
    for (t0, v0, _), (t1, v1, ease) in reversed(list(zip(vals, vals[1:]))):
        span = max(1e-3, t1 - t0)
        u = f"clip(({tvar}-{t0:.4f})/{span:.4f},0,1)"
        if ease == "doux":
            u = f"(({u})*({u})*(3-2*({u})))"
        expr = f"if(lt({tvar},{t1:.4f}),{v0:.5f}+({v1 - v0:.5f})*{u},{expr})"
    return f"if(lt({tvar},{vals[0][0]:.4f}),{vals[0][1]:.5f},{expr})"


def _cover_keyframes(W: int, H: int, kfs) -> str:
    """Zoom et cadrage animés : même géométrie que l'aperçu (image couvrante x zoom, puis cadre centré sur
    le point choisi, sans sortir de l'image)."""
    z, cx, cy = kf_expr(kfs, "zoom"), kf_expr(kfs, "x"), kf_expr(kfs, "y")
    s = f"max({W}/iw,{H}/ih)*({z})"
    return (f"scale=w='max({W},trunc(iw*{s}/2)*2)':h='max({H},trunc(ih*{s}/2)*2)':eval=frame:flags=bicubic,"
            f"crop={W}:{H}:x='clip(({cx})*iw-{W}/2,0,iw-{W})':y='clip(({cy})*ih-{H}/2,0,ih-{H})'")


def ramp_setpts(c) -> str:
    """Horodatage d'une rampe de vitesse : la vitesse passe progressivement de s0 à s1 sur le plan."""
    s0, s1 = c.ramp
    a = (s1 - s0) / (2 * c.duration)
    return f"setpts='(-{s0:.5f}+sqrt({s0 * s0:.6f}+4*({a:.6f})*(PTS-STARTPTS)*TB))/(2*({a:.6f}))/TB'"


def ramp_audio(c, src_label: str, tag: str, n: int | None = None) -> tuple[list[str], str]:
    """Son d'une rampe de vitesse : découpé en tranches, chacune à sa vitesse (atempo garde la hauteur)."""
    d = c.duration
    n = n or max(4, min(24, int(d * 4)))
    ts = [d * k / n for k in range(n + 1)]
    taus = [c.local_to_src(t) for t in ts]
    outs = [f"{tag}s{k}" for k in range(n)]
    parts = [f"{src_label}asplit={n}" + "".join(f"[{o}]" for o in outs)]
    pieces = []
    for k in range(n):
        sp = (taus[k + 1] - taus[k]) / max(1e-6, ts[k + 1] - ts[k])
        parts.append(f"[{outs[k]}]atrim=start={taus[k]:.4f}:end={taus[k + 1]:.4f},asetpts=PTS-STARTPTS,"
                     f"{fx.atempo_chain(sp)}[{tag}p{k}]")
        pieces.append(f"[{tag}p{k}]")
    parts.append("".join(pieces) + f"concat=n={n}:v=0:a=1[{tag}r]")
    return parts, f"[{tag}r]"


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
                  fps: int | None = None, smooth: bool = False,
                  three_d: bool = False, container: str = "mp4") -> tuple[list[str], float]:
    """three_d=True : la sortie vidéo (avant sous-titres) part en images brutes BGR sur stdout pour le
    moteur 3D, et le son va dans work_dir/audio.m4a. Sinon : un seul fichier final."""
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
            slowest = min(c.ramp) if c.ramp else speed
            src_rate = (m.fps or FPS) * slowest  # images source disponibles par seconde de timeline
            pts = ramp_setpts(c) if c.ramp else f"setpts=(PTS-STARTPTS)/{speed:.4f}"
            if smooth and src_rate < FPS - 1:
                # Interpolation de mouvement : crée les images manquantes (60-120 i/s, ralentis fluides).
                vchain.append(f"[{vin}:v:0]{pts},"
                              f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
            else:
                vchain.append(f"[{vin}:v:0]{pts},fps={FPS}")
            face_kf = None
            if any(mk.follow_dx is not None for mk in c.masks):
                face_kf = faces_an.keyframes((storage.load_signals(project.id, m.id) or {}).get("faces"),
                                             c.src_in, c.src_out, speed)
            vchain += mask_filters(c.masks, f"c{i}", face_kf)
            kf: list[tuple[float, float]] = []
            if c.keyframes:
                vchain.append(_cover_keyframes(W, H, c.keyframes))
            elif c.focus_x is not None:
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
            base = f"[{ain}:a:0]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo"
            if c.ramp:
                filters.append(base + f"[ra{i}]")
                parts, lab = ramp_audio(c, f"[ra{i}]", f"r{i}")
                filters += parts
                achain = [f"{lab}anull"]
            else:
                achain = [base]
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
        key = {"vert": "0x00FF00", "bleu": "0x0047FF"}.get(o.chroma)
        chroma = f"format=yuva420p,chromakey={key}:0.17:0.08," if key else ""
        if o.mode == "partage":
            # Écran partagé : le plan principal garde la moitié gauche (ou haute), centrée, et le plan de coupe
            # remplit l'autre moitié.
            side = W >= H
            hw, hh = (_even(W / 2), H) if side else (W, _even(H / 2))
            filters.append(f"[{vlabel}]split[sb{j}][ss{j}]")
            filters.append(f"[ss{j}]crop={hw}:{hh}:{(W - hw) // 2 if side else 0}:{0 if side else (H - hh) // 2}[sc{j}]")
            filters.append(f"[sb{j}][sc{j}]overlay=0:0:enable='between(t,{o.start:.3f},{o.start + od:.3f})'[sm{j}]")
            vlabel = f"sm{j}"
            fx_ = f"{o.focus_x:.4f}" if o.focus_x is not None else "0.5"
            prep = (f"scale={hw}:{hh}:force_original_aspect_ratio=increase:flags=bicubic,"
                    f"crop={hw}:{hh}:x='clip({fx_}*iw-{hw}/2,0,iw-{hw})':y='(ih-{hh})/2',"
                    + (f"drawbox=x=0:y=0:w=4:h=ih:color=white@0.9:t=fill" if side
                       else f"drawbox=x=0:y=0:w=iw:h=4:color=white@0.9:t=fill"))
            x, y = (f"{W - hw}", "0") if side else ("0", f"{H - hh}")
        elif o.mode == "incrustation":
            pw = _even(W * 0.34)
            ph = _even(pw * H / W)
            prep = (f"scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph}"
                    + ("" if key else f",pad={pw + 8}:{ph + 8}:4:4:color=white"))
            mx, my = int(W * 0.04), int(H * 0.05)
            x = f"W-w-{mx}" if o.corner.endswith("droite") else f"{mx}"
            y = f"{my}" if o.corner.startswith("haut") else f"H-h-{my}"
        else:
            prep = _cover(W, H)
            x, y = "0", "0"
        filters.append(
            f"[{oi}:v:0]setpts=PTS-STARTPTS,fps={FPS},{chroma}{prep},setsar=1,format=yuva420p,"
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

    # --- LUT d'étalonnage (.cube), dosée par lut_strength ---
    lut = luts_mod.resolve(project.id, timeline.lut)
    if lut:
        shutil.copyfile(lut, work_dir / "lut.cube")  # chemin relatif : le filtre lit depuis work_dir
        s = max(0.0, min(1.0, timeline.lut_strength))
        if s >= 0.999:
            filters.append(f"[{vlabel}]lut3d=file=lut.cube:interp=tetrahedral[vlut]")
        else:
            filters.append(f"[{vlabel}]split[lo][lb];[lb]lut3d=file=lut.cube:interp=tetrahedral[ll];"
                           f"[lo][ll]blend=all_expr='A*(1-{s:.3f})+B*{s:.3f}'[vlut]")
        vlabel = "vlut"

    # --- piste Images : logos, filigranes, stickers ---
    for k, g in enumerate(timeline.images):
        path = storage.assets_dir(project.id) / Path(g.asset).name
        if not path.exists() or g.start >= total:
            continue
        gd = min(total - g.start, g.duration if g.duration else total)
        inputs += ["-loop", "1", "-t", f"{gd:.3f}", "-i", str(path)]
        gi = n_in
        n_in += 1
        gw = _even(W * max(0.02, min(1.0, g.scale)))
        chain = [f"[{gi}:v]fps={FPS},scale={gw}:-2:flags=lanczos,format=rgba,"
                 f"colorchannelmixer=aa={max(0.0, min(1.0, g.opacity)):.3f}"]
        if g.anim != "aucune" and gd > 0.6:
            chain.append(f"fade=t=in:st=0:d=0.3:alpha=1,fade=t=out:st={gd - 0.3:.3f}:d=0.3:alpha=1")
        if g.anim == "pop":
            chain[0] = chain[0].replace(f"scale={gw}:-2:flags=lanczos",
                                        f"scale=w='{gw}*min(1,0.4+0.6*t/0.25+0.15*sin(PI*min(1,t/0.25)))':h=-2:eval=frame:flags=lanczos")
        chain.append(f"setpts=PTS-STARTPTS+{g.start:.3f}/TB[img{k}]")
        filters.append(",".join(chain))
        filters.append(f"[{vlabel}][img{k}]overlay=x='W*{g.x:.4f}-w/2':y='H*{g.y:.4f}-h/2':eof_action=pass:"
                       f"enable='between(t,{g.start:.3f},{g.start + gd:.3f})'[vimg{k}]")
        vlabel = f"vimg{k}"

    # --- sous-titres + piste Textes (un seul fichier ASS) ---
    tr = storage.load_translations(project.id, timeline.caption_language)
    if write_ass(timeline, transcripts, W, H, work_dir, tr) and not three_d:
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

    # Pistes audio : voix off, sons et musiques posés sur la timeline, mélangés à la voix avant la musique
    # (la musique s'efface donc aussi sous la voix off).
    items = [a for a in timeline.audio if a.start < total and a.duration > 0.05
             and (storage.audio_dir(project.id) / Path(a.asset).name).exists()]
    if items:
        labs = []
        for k, a in enumerate(items):
            path = storage.audio_dir(project.id) / Path(a.asset).name
            dur = min(a.duration, total - a.start)
            inputs += ["-ss", f"{a.src_in:.3f}", "-t", f"{dur:.3f}", "-i", str(path)]
            ms = int(round(a.start * 1000))
            fo = min(a.fade_out, dur / 2)
            filters.append(
                f"[{n_in}:a:0]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                f"volume={a.volume:.3f},afade=t=in:d={min(a.fade_in, dur / 2):.3f},"
                f"afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f},adelay={ms}|{ms}[au{k}]")
            labs.append(f"[au{k}]")
            n_in += 1
        filters.append(f"[{alabel}]{''.join(labs)}amix=inputs={len(labs) + 1}:duration=first:normalize=0[atr]")
        alabel = "atr"

    if music_path:
        skip = max(0.0, timeline.music.start or 0.0)
        inputs += (["-ss", f"{skip:.3f}"] if skip > 0 else []) + ["-stream_loop", "-1", "-i", music_path]
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

    if three_d:
        filters.append(f"[{vlabel}]format=bgr24[vraw]")
        script.write_text(";\n".join(filters), encoding="utf-8")
        args = [
            *inputs,
            "-filter_complex_script", str(script),
            "-map", "[aout]", *audio_codec_args(container), str(work_dir / f"audio.{'wav' if container == 'mov' else 'mka'}"),
            "-map", "[vraw]", "-f", "rawvideo", "-pix_fmt", "bgr24", "-r", str(FPS), "pipe:1",
        ]
        return args, total

    args = [
        *inputs,
        "-filter_complex_script", str(script),
        "-map", f"[{vlabel}]", "-map", "[aout]",
        *video_codec_args(container), "-r", str(FPS),
        *audio_codec_args(container),
        *(["-movflags", "+faststart"] if container in ("mp4", "mov") else []), str(out_path),
    ]
    return args, total


def write_ass(timeline: Timeline, transcripts: dict[str, Transcript], W: int, H: int, work_dir: Path,
              translations: dict[str, str] | None = None) -> bool:
    lines = cap.build_lines(timeline, transcripts, translations) if timeline.captions != "aucun" else []
    texts = [t for t in timeline.texts if t.style != "texte_3d"]
    if not (lines or texts):
        return False
    ass = cap.to_ass(lines, W, H, timeline.captions, timeline.format, texts)
    (work_dir / "captions.ass").write_text(ass, encoding="utf-8")
    return True


def render(project: Project, timeline: Timeline, transcripts: dict[str, Transcript], out_path: Path,
           work_dir: Path, quality: str = "1080p", music_path: str | None = None,
           on_progress: Callable[[float], None] | None = None, fps: int | None = None,
           smooth: bool = False, container: str = "mp4") -> Path:
    if container == "gif":
        return render_gif(project, timeline, transcripts, out_path, work_dir, quality, music_path, on_progress, fps)
    if scene3d.needs_3d(timeline):
        render_3d(project, timeline, transcripts, out_path, work_dir, quality, music_path, on_progress, fps, smooth,
                  container)
    else:
        args, total = build_command(project, timeline, transcripts, out_path, work_dir, quality, music_path,
                                    fps=fps, smooth=smooth, container=container)
        # Le filtre ass lit captions.ass en chemin relatif depuis work_dir (évite l'échappement des chemins).
        ffmpeg.run(args, total, on_progress, cwd=work_dir)
    tr = storage.load_translations(project.id, timeline.caption_language)
    srt = cap.to_srt(cap.build_lines(timeline, transcripts, tr))
    out_path.with_suffix(".srt").write_text(srt, encoding="utf-8")
    return out_path


def render_3d(project: Project, timeline: Timeline, transcripts: dict[str, Transcript], out_path: Path,
              work_dir: Path, quality: str, music_path: str | None, on_progress: Callable[[float], None] | None,
              fps: int | None, smooth: bool, container: str = "mp4") -> None:
    """Rendu avec le moteur 3D : FFmpeg (montage) -> images brutes -> scène 3D -> FFmpeg (textes + encodage)."""
    import subprocess
    import threading

    W, H = SIZES[(timeline.format, quality)]
    FPS = fps or source_fps(project, timeline)
    args, total = build_command(project, timeline, transcripts, out_path, work_dir, quality, music_path,
                                fps=FPS, smooth=smooth, three_d=True, container=container)
    has_ass = (work_dir / "captions.ass").exists()
    video_tmp = work_dir / f"video_3d.{CONTAINERS.get(container, CONTAINERS['mp4'])[0]}"
    audio_tmp = work_dir / f"audio.{'wav' if container == 'mov' else 'mka'}"
    dec = subprocess.Popen([ffmpeg.FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=work_dir)
    encp = subprocess.Popen([ffmpeg.FFMPEG, "-hide_banner", "-y", "-loglevel", "error",
                             "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
                             *(["-vf", "ass=captions.ass"] if has_ass else []),
                             *video_codec_args(container), "-r", str(FPS), str(video_tmp)],
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE, cwd=work_dir)
    errs: dict[str, bytes] = {}
    threading.Thread(target=lambda: errs.__setitem__("dec", dec.stderr.read()), daemon=True).start()
    threading.Thread(target=lambda: errs.__setitem__("enc", encp.stderr.read()), daemon=True).start()
    bg = None
    if timeline.background:
        from ..analysis import segment
        bg = segment.cover_image(storage.assets_dir(project.id) / Path(timeline.background).name, W, H)
    scene = scene3d.Scene3D(timeline, W, H, FPS, background=bg, assets_dir=storage.assets_dir(project.id))
    size = W * H * 3
    n_total = max(1, int(round(total * FPS)))
    n = 0
    try:
        while True:
            buf = dec.stdout.read(size)
            if not buf or len(buf) < size:
                break
            frame = np.frombuffer(bytearray(buf), np.uint8).reshape(H, W, 3)
            encp.stdin.write(np.ascontiguousarray(scene.process(n, frame)).tobytes())
            n += 1
            if on_progress and n % 10 == 0:
                on_progress(min(0.97, 0.97 * n / n_total))
    finally:
        try:
            encp.stdin.close()
        except BrokenPipeError:
            pass
    rc_dec, rc_enc = dec.wait(), encp.wait()
    if rc_dec != 0 or rc_enc != 0:
        msg = (errs.get("dec", b"") + errs.get("enc", b"")).decode(errors="replace").strip()
        raise ffmpeg.FFmpegError(msg[-1500:] or "Le rendu 3D a échoué")
    ffmpeg.run(["-i", str(video_tmp), "-i", str(audio_tmp), "-map", "0:v:0", "-map", "1:a:0",
                "-c", "copy", "-shortest", *(["-movflags", "+faststart"] if container in ("mp4", "mov") else []),
                str(out_path)], cwd=work_dir)
    if on_progress:
        on_progress(1.0)


def render_gif(project: Project, timeline: Timeline, transcripts: dict[str, Transcript], out_path: Path,
               work_dir: Path, quality: str, music_path: str | None,
               on_progress: Callable[[float], None] | None, fps: int | None) -> Path:
    """GIF animé (sans son) : rendu MP4 puis palette optimisée, largeur limitée pour garder un poids raisonnable."""
    tmp = work_dir / "gif_source.mp4"
    sub = (lambda x: on_progress(0.85 * x)) if on_progress else None
    render(project, timeline, transcripts, tmp, work_dir, quality, music_path, sub, fps=min(fps or 15, 25))
    W, H = SIZES[(timeline.format, quality)]
    w = min(W, 640 if W >= H else 480)
    rate = min(fps or 15, 25)
    ffmpeg.run(["-i", str(tmp), "-vf", f"fps={rate},scale={w}:-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];"
                f"[b][p]paletteuse=dither=bayer:bayer_scale=4", "-loop", "0", str(out_path)], cwd=work_dir)
    if on_progress:
        on_progress(1.0)
    return out_path
