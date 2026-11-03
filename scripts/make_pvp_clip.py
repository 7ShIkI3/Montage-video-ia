"""Génère un clip de test « gameplay PvP en voxels » (vue à la première personne, combats, coups,
éliminations, sons) avec le moteur 3D de l'app. Sert à tester le style d'edit PvP sans vidéo réelle.

Graphismes et sons sont entièrement synthétiques (aucune ressource d'un jeu existant).

Usage : python scripts/make_pvp_clip.py sortie.mp4 [--seconds 40] [--fps 60]
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys
import wave
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.render import engine3d as e3  # noqa: E402

RW, RH = 960, 540  # résolution de rendu, agrandie à la sortie
NEAR = 0.08

# Scénario : (début, fin, adversaire, coups donnés, coups reçus, élimination)
FIGHTS = [
    (6.0, 13.6, 0, [6.9, 7.5, 8.2, 9.0, 9.6, 10.9, 11.6, 12.3, 13.0], [8.6, 11.2], 13.3),
    (19.0, 26.6, 1, [19.8, 20.4, 21.1, 22.6, 23.1, 23.7, 24.6, 25.3, 25.9], [21.9, 22.2, 24.2], 26.2),
    (31.0, 37.8, 2, [31.6, 32.0, 32.4, 32.8, 33.3, 33.7, 34.1, 35.2, 35.6, 36.0, 36.5, 37.0], [34.6], 37.4),
]
SHIRTS = [(40, 90, 210), (200, 50, 50), (60, 170, 80)]  # RGB


def look_at_yaw(p, q) -> float:
    return math.degrees(math.atan2(q[0] - p[0], q[2] - p[2]))


def world_to_cam(P: np.ndarray, pos: np.ndarray, yaw: float, pitch: float, roll: float = 0.0) -> np.ndarray:
    R = e3.rotation(pitch, yaw, roll)
    return (P - pos) @ R  # équivaut à R^T (P - pos)


def clip_near(poly: np.ndarray) -> np.ndarray | None:
    """Découpe un polygone (N x 3, repère caméra) contre le plan z = NEAR."""
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ina, inb = a[2] >= NEAR, b[2] >= NEAR
        if ina:
            out.append(a)
        if ina != inb:
            t = (NEAR - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
    return np.array(out) if len(out) >= 3 else None


BOX_FACES = [  # indices des sommets, normale
    ((0, 1, 2, 3), (0, 0, -1)), ((5, 4, 7, 6), (0, 0, 1)), ((4, 0, 3, 7), (-1, 0, 0)),
    ((1, 5, 6, 2), (1, 0, 0)), ((4, 5, 1, 0), (0, 1, 0)), ((3, 2, 6, 7), (0, -1, 0)),
]
SUN = np.array([0.4, 0.8, -0.45])
SUN /= np.linalg.norm(SUN)


def box(center, size, R=np.eye(3)):
    cx, cy, cz = center
    sx, sy, sz = (s / 2 for s in size)
    v = np.array([[-sx, sy, -sz], [sx, sy, -sz], [sx, -sy, -sz], [-sx, -sy, -sz],
                  [-sx, sy, sz], [sx, sy, sz], [sx, -sy, sz], [-sx, -sy, sz]])
    return v @ R.T + [cx, cy, cz], R


class World:
    def __init__(self, seed=3):
        rng = np.random.default_rng(seed)
        self.polys: list[tuple[np.ndarray, tuple]] = []
        N = 26
        h = np.zeros((N, N), int)
        for _ in range(9):  # petites collines
            x, z = rng.integers(0, N, 2)
            h[max(0, x - 2):x + 3, max(0, z - 2):z + 3] += 1
        h = np.minimum(h, 2)
        h[9:17, 9:17] = 0  # arène plate au centre
        self.h = h
        for i in range(N):
            for j in range(N):
                x, z = i - N / 2, j - N / 2
                y = float(h[i, j])
                g = int(rng.integers(-12, 12))
                top = (95 + g, 160 + g, 70 + g)
                self.polys.append((np.array([[x, y, z], [x + 1, y, z], [x + 1, y, z + 1], [x, y, z + 1]]), top))
                for (di, dj, quadf) in ((1, 0, lambda: [[x + 1, y, z], [x + 1, y, z + 1], [x + 1, 0, z + 1], [x + 1, 0, z]]),
                                        (-1, 0, lambda: [[x, y, z + 1], [x, y, z], [x, 0, z], [x, 0, z + 1]]),
                                        (0, 1, lambda: [[x + 1, y, z + 1], [x, y, z + 1], [x, 0, z + 1], [x + 1, 0, z + 1]]),
                                        (0, -1, lambda: [[x, y, z], [x + 1, y, z], [x + 1, 0, z], [x, 0, z]])):
                    ni, nj = i + di, j + dj
                    nh = h[ni, nj] if 0 <= ni < N and 0 <= nj < N else 0
                    if y > nh:
                        q = np.array(quadf(), float)
                        q[2:, 1] = nh
                        self.polys.append((q, (120 + g, 85 + g, 55 + g)))
        for (tx, tz) in ((-8, -6), (7, -8), (-6, 8), (9, 6), (-11, 1), (3, 11)):  # arbres
            base = float(h[int(tx + N / 2), int(tz + N / 2)])
            v, _ = box((tx + 0.5, base + 1.5, tz + 0.5), (0.8, 3, 0.8))
            self._box_polys(v, (110, 80, 50))
            v, _ = box((tx + 0.5, base + 3.6, tz + 0.5), (3, 1.8, 3))
            self._box_polys(v, (50, 120, 45))

    def _box_polys(self, v, col):
        for idx, n in BOX_FACES:
            self.polys.append((v[list(idx)], col, np.array(n, float)))

    def ground(self, x, z) -> float:
        N = self.h.shape[0]
        i, j = int(math.floor(x + N / 2)), int(math.floor(z + N / 2))
        return float(self.h[i, j]) if 0 <= i < N and 0 <= j < N else 0.0


def fighter_polys(pos, yaw, shirt, walk, hurt, fall):
    """Combattant en blocs : tête, corps, bras, jambes. hurt = teinte rouge, fall = 0..1 tombe."""
    R = e3.rotation(0, yaw, 0) @ e3.rotation(0, 0, 0)
    Rf = e3.rotation(-85 * fall, 0, 0)
    skin, pants = (225, 180, 140), (50, 50, 120)
    sw = math.sin(walk) * 35
    parts = [((0, 1.75, 0), (0.5, 0.5, 0.5), skin, 0), ((0, 1.125, 0), (0.5, 0.75, 0.26), shirt, 0),
             ((-0.37, 1.125, 0), (0.24, 0.75, 0.24), shirt, sw), ((0.37, 1.125, 0), (0.24, 0.75, 0.24), shirt, -sw),
             ((-0.13, 0.375, 0), (0.25, 0.75, 0.25), pants, -sw), ((0.13, 0.375, 0), (0.25, 0.75, 0.25), pants, sw)]
    out = []
    for c, size, col, swing in parts:
        pivot_y = c[1] + size[1] / 2 if swing else c[1]
        Rs = e3.rotation(swing, 0, 0)
        v, _ = box((0, c[1] - pivot_y, 0), size)
        v = v @ Rs.T + [c[0], pivot_y, c[2]]
        v = v @ Rf.T
        v = v @ R.T + pos
        if hurt > 0:
            col = tuple(int(a * (1 - hurt) + b * hurt) for a, b in zip(col, (255, 40, 40)))
        for idx, n in BOX_FACES:
            out.append((v[list(idx)], col, (R @ Rf @ np.array(n, float))))
    return out


def render_scene(img, polys, pos, yaw, pitch, roll, cam):
    items = []
    for P, col, *nrm in polys:
        C = world_to_cam(P, pos, yaw, pitch, roll)
        if (C[:, 2] < NEAR).all():
            continue
        if nrm:
            n = nrm[0]
            if np.dot(n, P.mean(0) - pos) > 0:
                continue  # face arrière
        else:
            e1, e2 = P[1] - P[0], P[2] - P[0]
            n = np.cross(e2, e1)
            n = n / (np.linalg.norm(n) + 1e-9)
            if np.dot(n, P.mean(0) - pos) > 0:
                n = -n
        Cc = clip_near(C)
        if Cc is None:
            continue
        depth = float(np.linalg.norm(P.mean(0) - pos))
        shade = 0.55 + 0.45 * max(0.0, float(np.dot(n, SUN)))
        fog = min(1.0, max(0.0, (depth - 9) / 14))
        rgb = np.array(col, float) * shade * (1 - fog) + np.array([170, 205, 245]) * fog
        items.append((depth, Cc, rgb))
    items.sort(key=lambda x: -x[0])
    for _, Cc, rgb in items:
        pts = cam.project(Cc)
        cv2.fillPoly(img, [np.int32(pts * 4)], tuple(int(c) for c in rgb[::-1]), lineType=cv2.LINE_8, shift=2)


def sky(H, W):
    y = np.linspace(0, 1, H)[:, None, None]
    top, bot = np.array([235, 160, 90], float), np.array([250, 215, 175], float)  # BGR
    return np.broadcast_to(top * (1 - y) + bot * y, (H, W, 3)).astype(np.uint8)


def draw_hud(img, hp, swing):
    H, W = img.shape[:2]
    for k in range(10):  # vie
        x0, y0 = int(W * 0.3) + k * 22, H - 58
        col = (40, 40, 220) if k < hp else (60, 60, 60)
        cv2.rectangle(img, (x0, y0), (x0 + 16, y0 + 14), col, -1)
        cv2.rectangle(img, (x0, y0), (x0 + 16, y0 + 14), (20, 20, 20), 1)
    for k in range(9):  # barre d'objets
        x0 = int(W * 0.3) + k * 44
        cv2.rectangle(img, (x0, H - 38), (x0 + 40, H - 4), (90, 90, 90) if k else (230, 230, 230), 2)
    # épée tenue en main : lame qui frappe vers le centre
    a = math.sin(min(1.0, swing) * math.pi) if swing < 1 else 0.0
    cx, cy = int(W * 0.78 - 170 * a), int(H * 0.86 - 120 * a)
    ang = math.radians(-35 - 50 * a)
    tip = (int(cx + math.cos(ang) * 230), int(cy + math.sin(ang) * 230))
    cv2.line(img, (cx, cy), tip, (40, 40, 40), 22, cv2.LINE_AA)
    cv2.line(img, (cx, cy), tip, (215, 235, 240), 14, cv2.LINE_AA)
    cv2.line(img, (cx - 26, cy + 18), (cx + 26, cy - 18), (40, 90, 140), 14, cv2.LINE_AA)
    cv2.line(img, (cx, cy), (int(cx - math.cos(ang) * 50), int(cy - math.sin(ang) * 50)), (40, 70, 110), 16, cv2.LINE_AA)
    cv2.drawMarker(img, (W // 2, H // 2), (255, 255, 255), cv2.MARKER_CROSS, 18, 2)


def synth_audio(seconds, sr=48000, seed=7):
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    out = np.zeros(n)
    wind = rng.normal(0, 1, n)
    k = np.ones(400) / 400
    out += np.convolve(wind, k, "same") * 0.25

    def put(t, sig):
        i = int(t * sr)
        j = min(n, i + len(sig))
        if i < n:
            out[i:j] += sig[: j - i]

    def env(d, k):
        tt = np.arange(int(d * sr)) / sr
        return tt, np.exp(-k * tt)

    in_fight = lambda t: any(a <= t <= b for a, b, *_ in FIGHTS)
    t = 0.3
    while t < seconds:  # pas
        if not in_fight(t):
            tt, e = env(0.09, 60)
            put(t, rng.normal(0, 1, len(tt)) * e * 0.12)
        t += 0.38
    for a, b, _o, hits, hurts, kill in FIGHTS:
        for h in hits:
            tt, e = env(0.18, 30)
            whoosh = np.convolve(rng.normal(0, 1, len(tt)), np.ones(8) / 8, "same") * np.sin(np.pi * tt / 0.18) * 0.25
            put(h - 0.12, whoosh)
            tt, e = env(0.25, 22)
            thud = (np.sin(2 * np.pi * (140 - 200 * tt) * tt) * 0.9 + rng.normal(0, 1, len(tt)) * 0.5 * np.exp(-60 * tt)) * e
            put(h, thud * 0.95)
        for h in hurts:
            tt, e = env(0.3, 14)
            put(h, np.sin(2 * np.pi * (220 - 160 * tt) * tt) * e * 0.6)
        tt, e = env(0.9, 4)
        put(kill, (np.sin(2 * np.pi * 880 * tt) + 0.6 * np.sin(2 * np.pi * 1320 * tt)) * e * 0.35)
    out = np.clip(out, -1, 1)
    return (out * 32767 * 0.8).astype(np.int16), sr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--size", default="1280x720")
    a = ap.parse_args()
    OW, OH = map(int, a.size.split("x"))
    out = Path(a.out)
    world = World()
    cam = e3.Camera(RW, RH, 70)
    bg = sky(RH, RW)
    wav = out.with_suffix(".wav")
    pcm, sr = synth_audio(a.seconds)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{OW}x{OH}", "-r", str(a.fps), "-i", "pipe:0", "-i", str(wav),
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-b:a", "160k", "-shortest", str(out)], stdin=subprocess.PIPE)
    pos = np.array([0.0, 1.62, -9.0])
    yaw = 0.0
    hp = 10
    for f in range(int(a.seconds * a.fps)):
        t = f / a.fps
        fight = next((F for F in FIGHTS if F[0] - 1.5 <= t <= F[1] + 1.0), None)
        polys = list(world.polys)
        pitch = 4.0
        roll = 0.0
        swing = 9.0
        if fight:
            st, en, k, hits, hurts, kill = fight
            ang = t * 0.9 + k * 2.0
            dist = 2.6 + 0.6 * math.sin(t * 2.3)
            target = pos + np.array([0.0, 0, 0])
            # l'adversaire tourne autour du joueur ; le joueur le suit du regard
            opp = np.array([pos[0] + math.sin(ang) * dist, 0.0, pos[2] + math.cos(ang) * dist])
            if t < st:
                opp = np.array([pos[0] + math.sin(ang) * (dist + (st - t) * 5), 0.0, pos[2] + math.cos(ang) * (dist + (st - t) * 5)])
            want = look_at_yaw(pos, opp)
            yaw += ((want - yaw + 180) % 360 - 180) * 0.25
            hurt = max((max(0.0, 1 - (t - h) / 0.25) for h in hits if 0 <= t - h), default=0.0)
            fall = min(1.0, max(0.0, (t - kill) / 0.4))
            kb = sum(0.5 * math.exp(-(t - h) * 8) for h in hits if 0 <= t - h < 0.6)
            opp += np.array([math.sin(ang), 0, math.cos(ang)]) * kb
            if t < kill + 0.9:
                opp[1] = world.ground(opp[0], opp[2])
                polys += fighter_polys(opp, want + 180, SHIRTS[k], t * 9, hurt, fall)
            swing = min((t - h + 0.12) / 0.25 for h in hits if t - h + 0.12 >= 0) if any(t - h + 0.12 >= 0 for h in hits) else 9
            for h in hurts:
                if 0 <= t - h < 0.3:
                    roll = 8 * (1 - (t - h) / 0.3)
            hp = 10 - sum(1 for h in hurts if t >= h) * 2 + sum(3 for _ in [1] if t > kill)
            hp = max(2, min(10, hp))
        else:
            pos += np.array([math.sin(math.radians(yaw)), 0, math.cos(math.radians(yaw))]) * (3.2 / a.fps)
            pos[0] = float(np.clip(pos[0], -7, 7))
            pos[2] = float(np.clip(pos[2], -7, 7))
            yaw += 25 / a.fps * math.sin(t * 0.4)
            pitch = 4 + 2 * math.sin(t * 3)
        pos[1] = 1.62 + world.ground(pos[0], pos[2]) + (0.04 * math.sin(t * 11) if not fight else 0)
        img = bg.copy()
        render_scene(img, polys, pos, yaw, pitch, roll, cam)
        if fight:
            for h in fight[4]:
                if 0 <= t - h < 0.3:
                    a_ = 0.45 * (1 - (t - h) / 0.3)
                    img[:] = (img * (1 - a_) + np.array([30, 30, 220]) * a_).astype(np.uint8)
        frame = cv2.resize(img, (OW, OH), interpolation=cv2.INTER_LINEAR)
        draw_hud(frame, hp, swing)
        enc.stdin.write(frame.tobytes())
    enc.stdin.close()
    enc.wait()
    wav.unlink(missing_ok=True)
    print(out)


if __name__ == "__main__":
    main()
