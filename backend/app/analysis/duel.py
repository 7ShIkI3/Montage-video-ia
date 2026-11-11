"""Qui porte le combo ? Deux mesures locales (sans IA, sans appel payant) sur les rafales de coups d'un rush PvP.

Le son d'un coup est le même que le joueur le donne ou le reçoive : une rafale parfaite peut être l'adversaire qui
enchaîne le joueur. Deux indices se lisent pourtant dans l'image, en première personne :

- la vie : la barre de cœurs de Minecraft est lue sur les pixels (position et échelle détectées, pas codées en
  dur). Une vie qui s'effondre pendant la rafale = le joueur subit ; une vie qui tient = il donne probablement.
  Une vie pleine du début à la fin ne prouve rien (mode Boxing, sans dégâts) : « inconnu ».
- la caméra : quand le joueur est touché, la vue penche d'environ 14 degrés et revient en une demi-seconde. Aucune
  autre cause ne fait rouler la caméra : chaque impulsion de roulis est un coup reçu. Une fenêtre d'au moins 1,5 s
  avec des coups au son et aucune impulsion = enchaînement donné.

Verdict d'une rafale : « donne », « subit », « echange » ou « inconnu ». Règle de prudence : tout doute (barre
introuvable, image illisible, jeu différent, interface masquée) donne « inconnu », jamais « donne ». « subit » est un
veto : il suffit qu'une des deux mesures le dise. Le résultat de chaque rafale est gardé avec les signaux du rush
(`signals["duel"]`) : refaire un montage ne relit aucune image. Seules les images autour des rafales sont lues.
La courbe de vie de chaque rafale est gardée avec son résultat (`vie["courbe"]`) : le montage s'en sert pour ne jamais
monter un passage où le joueur perd plus d'un cœur (`fenetre_sans_chute`).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from .. import storage
from ..media.ffmpeg import FFMPEG

log = logging.getLogger(__name__)

VERSION = 2  # change si les règles de lecture changent : les résultats gardés sont alors recalculés
LABELS = ("donne", "subit", "echange", "inconnu")

# --- vie ---------------------------------------------------------------------------------------------------
FPS_VIE = 10             # images lues par seconde dans une rafale
MARGE = 0.5              # secondes lues avant et après la rafale
SEUIL_DONNE = 0.5        # perte max (pic -> creux, en cœurs) tolérée pour « donne » : un demi-cœur de bruit de lecture,
                         # pas plus (un cœur perdu, c'est un coup reçu : ce n'est plus un enchaînement sans réponse)
SEUIL_SUBIT = 3.0        # perte à partir de laquelle le joueur subit
SEUIL_PRESQUE_MORT = 1.5  # vie finale sous ce seuil + perte >= SEUIL_SUBIT_BAS => subit
SEUIL_SUBIT_BAS = 2.0
FENETRE_CONTEXTE = 15.0  # secondes de part et d'autre où la vie pleine trahit un mode sans dégâts
MAX_INVALIDES = 0.5      # au-delà, la série de vie d'une rafale est jugée illisible
MARGE_COURBE = 1.0       # secondes de courbe de vie gardées après la rafale (le ralenti du coup final les dépasse)
PERTE_MAX_PLAN = 1.0     # cœurs perdus au plus (pic -> creux) sur un passage monté : au-delà, on y voit le joueur encaisser

# --- caméra ------------------------------------------------------------------------------------------------
W, H, FPS_ROULIS = 480, 270, 12
SEUIL_PIC = 4.0          # saut de roulis (degrés par pas de 1/12 s) compté comme un coup reçu
PIC_MAX = 22.0           # au-delà : suivi aberrant (caméra retournée, mort), pas un coup
DECALAGE_AVANT = 0.35    # une impulsion précède de 0 à 0,35 s le son du coup qu'elle accompagne
DECALAGE_APRES = 0.08
TROU_MIN = 1.5           # fenêtre sans impulsion d'au moins 1,5 s pour parler d'enchaînement donné
COUPS_MIN = 4            # au moins 4 coups sonores dans cette fenêtre
FIABLE_MIN = 0.95        # suivi exploitable sur au moins 95 % de la fenêtre (sinon on ne tranche pas « donne »)
PART_SUBIT = 0.85        # part de coups reçus à partir de laquelle la rafale est subie
# zone de suivi : centre de l'écran, hors interface (panneau latéral, barre d'objets, chat) et hors main
_MASQUE = np.zeros((H, W), np.uint8)
_MASQUE[8:160, 70:370] = 255
_MASQUE[100:160, 300:370] = 0

# --- sélection ---------------------------------------------------------------------------------------------
ECHANGE_COUT_MAX = 0.5   # cœurs perdus au plus pour qu'un échange reste un bon moment (voir SEUIL_DONNE)
ECHANGE_PART_MAX = 0.5   # part de coups reçus à partir de laquelle ce n'est plus un bon moment (la moitié ou plus)


# =============================================================================================================
# Lecture de la barre de vie
# =============================================================================================================

def _masque_vie(img: np.ndarray) -> np.ndarray:
    """Pixels « cœur plein » : rouge (pack classique) ou cyan (pack de textures du joueur). Les rapports G/R et
    B/R écartent les fonds orange, olive ou ciel. Poison et wither ne sont pas lus."""
    b = img[..., 0].astype(np.int16)
    g = img[..., 1].astype(np.int16)
    r = img[..., 2].astype(np.int16)
    rouge = (r > 100) & (g * 5 < r) & (b * 5 < r)
    cyan = (g > 120) & (b > 120) & (r < 60) & (np.abs(g - b) < 40)
    return rouge | cyan


def _masque_absorption(img: np.ndarray) -> np.ndarray:
    b = img[..., 0].astype(np.int16)
    g = img[..., 1].astype(np.int16)
    r = img[..., 2].astype(np.int16)
    return (r > 190) & (g > 140) & (b < 80) & (r > g + 10)


@dataclass
class Layout:
    """Cœur i : sprite de 9s x 9s dont le coin haut-gauche est à (x0 + 8*s*i, y0)."""
    w: int
    h: int
    s: float          # échelle d'interface (pixels par pixel d'interface)
    x0: float
    y0: float
    score: float      # qualité de la détection (0..1)

    def crop(self) -> tuple[int, int, int, int]:
        """Rectangle ffmpeg (largeur, hauteur, x, y) qui contient la rangée de cœurs, avec une marge pour le
        tremblement des cœurs à basse vie."""
        s = self.s
        x = int(max(0, np.floor(self.x0 - 2 * s)))
        y = int(max(0, np.floor(self.y0 - 3 * s)))
        cw = int(min(self.w - x, np.ceil(8 * s * 9 + 9 * s + 4 * s)))
        ch = int(min(self.h - y, np.ceil(9 * s + 6 * s)))
        return cw & ~1, ch & ~1, x, y

    def decalage(self) -> tuple[float, float]:
        cw, ch, x, y = self.crop()
        return self.x0 - x, self.y0 - y

    def zone_jury(self) -> tuple[float, float, float, float]:
        """Zone (x, y, largeur, hauteur) en fractions de l'image : armure, cœurs et faim, pour le jury."""
        s = self.s
        x = max(0.0, self.x0 - 4 * s)
        y = max(0.0, self.y0 - 14 * s)
        w = min(self.w - x, 190 * s)
        h = min(self.h - y, 27 * s)
        return (round(x / self.w, 4), round(y / self.h, 4), round(w / self.w, 4), round(h / self.h, 4))


ECHELLES = [round(0.75 + 0.125 * k, 3) for k in range(43)]  # 0,75 .. 6,0 par pas de 0,125


def _modele_barre(s: float, taille: tuple[int, int]) -> np.ndarray:
    """Image modèle : dix rectangles intérieurs de cœur à l'échelle s."""
    h, w = taille
    m = np.zeros((h, w), np.float32)
    for i in range(10):
        x = 8 * s * i
        cv2.rectangle(m, (int(round(x + 1.5 * s)), int(round(1.5 * s))),
                      (int(round(x + 7.5 * s)), int(round(7.0 * s))), 1.0, -1)
    return m


def _essai_echelle(masque: np.ndarray, w: int, h: int, by: int, s: float) -> Layout | None:
    cx, cy = w / 2 - 91 * s, h - 39 * s
    mx = int(round(3 * s)) + 3
    x_min, y_min = int(cx - mx), int(cy - mx) - by
    tw, th = int(8 * s * 9 + 9 * s), int(9 * s)
    if y_min < 0 or x_min < 0:
        return None
    zone = masque[y_min: y_min + 2 * mx + th, x_min: x_min + 2 * mx + tw]
    if zone.shape[0] < th or zone.shape[1] < tw:
        return None
    modele = _modele_barre(s, (th, tw))
    res = cv2.matchTemplate(zone, modele, cv2.TM_CCORR_NORMED)
    _, _, _, loc = cv2.minMaxLoc(res)
    dedans = zone[loc[1]: loc[1] + th, loc[0]: loc[0] + tw]
    # précision (le modèle est bien rempli) x rappel (le masque est expliqué par le modèle) : écarte les mauvaises
    # échelles, dont les rectangles ne tombent pas sur les cœurs
    prec = float((dedans * modele).sum() / max(modele.sum(), 1.0))
    rapp = float((dedans * modele).sum() / max(masque.sum(), 1.0))
    return Layout(w, h, s, float(x_min + loc[0]), float(y_min + loc[1] + by), prec * rapp ** 0.5)


def _layout_image(img: np.ndarray) -> Layout | None:
    """Meilleure (échelle, position) de la barre de cœurs dans une image pleine résolution : échelle grossière,
    affinage, puis aimantation sur une échelle entière (l'interface de Minecraft n'a que celles-là)."""
    h, w = img.shape[:2]
    by = int(h * 0.80)
    masque = _masque_vie(img[by:, :]).astype(np.float32)
    if masque.sum() < 200:  # pas (assez) de cœurs sur cette image
        return None
    meilleur: Layout | None = None
    for s in ECHELLES:
        lay = _essai_echelle(masque, w, h, by, s)
        if lay is not None and (meilleur is None or lay.score > meilleur.score):
            meilleur = lay
    if meilleur is None:
        return None
    for k in range(-3, 4):
        lay = _essai_echelle(masque, w, h, by, meilleur.s + k * 0.0417) if k else None
        if lay is not None and lay.score > meilleur.score:
            meilleur = lay
    entier = _essai_echelle(masque, w, h, by, float(round(meilleur.s)))
    if entier is not None and abs(meilleur.s - round(meilleur.s)) <= 0.15 and entier.score >= 0.92 * meilleur.score:
        meilleur = entier
    return meilleur


def _image_pleine(path: str, t: float, w: int, h: int) -> np.ndarray | None:
    p = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path, "-an",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True)
    if len(p.stdout) < w * h * 3:
        return None
    return np.frombuffer(p.stdout[: w * h * 3], np.uint8).reshape(h, w, 3)


def detecter_layout(path: str, w: int, h: int, duree: float, suffisant: float = 0.6) -> Layout | None:
    """Trouve échelle et position de la barre de cœurs en regardant quelques images réparties sur le rush (lues
    une à une, on s'arrête dès que la barre est bien détectée). None si aucune image ne montre de cœurs lisibles :
    interface masquée, autre jeu."""
    if w <= 0 or h <= 0 or duree <= 0:
        return None
    meilleur = None
    for t in np.linspace(min(5.0, duree / 2), max(duree - 5, min(6.0, duree)), 14):
        img = _image_pleine(path, float(t), w, h)
        lay = _layout_image(img) if img is not None else None
        if lay is not None and (meilleur is None or lay.score > meilleur.score):
            meilleur = lay
        if meilleur is not None and meilleur.score >= suffisant:
            break
    return meilleur if meilleur is not None and meilleur.score >= 0.15 else None


def lire_vie(crops: np.ndarray, lay: Layout) -> np.ndarray:
    """crops : (N, ch, cw, 3) BGR. Retourne la vie en cœurs (nan si l'image est illisible)."""
    n = len(crops)
    s = lay.s
    ox, oy = lay.decalage()
    vie = np.zeros(n, np.float32)
    lisibles = np.zeros(n, np.int32)
    plein = _masque_vie(crops)
    jaune = _masque_absorption(crops)
    sombre = crops.max(axis=-1) < 110  # intérieur de cœur vide (gris foncé)
    y1, y2 = int(round(oy + 2 * s)), int(round(oy + 7 * s))
    for i in range(10):
        xa = ox + 8 * s * i
        # moitié gauche / droite d'un cœur : un demi-cœur colore le côté gauche jusqu'un peu au-delà du centre
        for xs, xe in [(int(round(xa + 1.5 * s)), int(round(xa + 5 * s))),
                       (int(round(xa + 6 * s)), int(round(xa + 8.5 * s)))]:
            aire = max((xe - xs) * (y2 - y1), 1)
            fp = plein[:, y1:y2, xs:xe].sum(axis=(1, 2)) / aire
            fs = sombre[:, y1:y2, xs:xe].sum(axis=(1, 2)) / aire
            fj = jaune[:, y1:y2, xs:xe].sum(axis=(1, 2)) / aire
            vie += 0.5 * (fp > 0.2)
            lisibles += ((fp > 0.2) | (fs > 0.25) | (fj > 0.2)).astype(np.int32)
    # Une image est lisible si presque toutes les demi-cases sont colorées ou sombres (barre visible) ; sinon flash
    # blanc, menu, écran de mort. Une vie lue à 0 n'est jamais affichée en vrai : c'est une scène sombre.
    return np.where((lisibles >= 16) & (vie > 0), vie, np.nan)


def _lire_flux(path: str, t0: float, t1: float, lay: Layout, fps: float, cles_seules: bool = False):
    """Décode [t0, t1], rogne la barre de cœurs dans ffmpeg et lit la vie. Renvoie (instants, vie).
    `cles_seules` : seulement les images clés (une toutes les ~2 s), pour balayer large à peu de frais."""
    cw, ch, x, y = lay.crop()
    cmd = [FFMPEG, "-nostdin", "-v", "info" if cles_seules else "error", "-skip_frame", "nokey" if cles_seules else "bidir"]
    if cles_seules:
        cmd += ["-copyts"]
    cmd += ["-ss", f"{max(t0, 0):.3f}", "-t", f"{max(t1 - t0, 0.1):.3f}", "-i", path, "-an",
            "-vf", (f"crop={cw}:{ch}:{x}:{y},showinfo" if cles_seules else f"fps={fps},crop={cw}:{ch}:{x}:{y}")]
    if cles_seules:
        cmd += ["-fps_mode", "passthrough"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.run(cmd, capture_output=True)
    taille = cw * ch * 3
    n = len(p.stdout) // taille
    if n == 0:
        return np.zeros(0), np.zeros(0, np.float32)
    crops = np.frombuffer(p.stdout[: n * taille], np.uint8).reshape(n, ch, cw, 3)
    vie = lire_vie(crops, lay)
    if cles_seules:
        ts = [float(m) for m in re.findall(rb"pts_time:\s*([0-9.]+)", p.stderr)]
        if len(ts) != n:  # filet de sécurité : dates régulières
            ts = list(t0 + np.arange(n) * 2.0)
        return np.array(ts[:n]), vie
    return max(t0, 0.0) + np.arange(n) / fps, vie


def _nettoyer(vie: np.ndarray) -> np.ndarray:
    """Interpolation des trous (images illisibles) puis médiane sur 3 images."""
    v = vie.astype(np.float64).copy()
    ok = ~np.isnan(v)
    if ok.sum() == 0:
        return v
    idx = np.arange(len(v))
    v[~ok] = np.interp(idx[~ok], idx[ok], v[ok])
    if len(v) >= 3:
        p = np.pad(v, 1, mode="edge")
        v = np.median(np.stack([p[:-2], p[1:-1], p[2:]]), axis=0)
    return v


def descripteurs(vie: np.ndarray, fps: float = FPS_VIE) -> dict:
    """Mesures sur la série de vie d'une rafale (déjà lue)."""
    if len(vie) < 3 or np.isnan(vie).mean() > MAX_INVALIDES:
        return {"valide": False}
    v = _nettoyer(vie)
    pic = np.maximum.accumulate(v)
    perte = float((pic - v).max())  # pic -> creux (le creux vient après le pic) : un soin ne l'annule pas
    k = max(1, int(round(fps)))
    soin = bool(len(v) > k and (v[k:] - v[:-k]).max() >= 3.0)  # +3 cœurs en 1 s : potion de soin, réapparition
    return {"valide": True, "debut": float(v[0]), "fin": float(v[-1]), "min": float(v.min()),
            "perte": perte, "soin": soin}


def verdict_vie(d: dict, sans_degats: bool) -> str:
    """donne | subit | echange | inconnu. Tout ce qui est douteux tombe dans « inconnu » ou « echange »."""
    if not d.get("valide"):
        return "inconnu"
    if d["perte"] >= SEUIL_SUBIT or (d["min"] <= SEUIL_PRESQUE_MORT and d["perte"] >= SEUIL_SUBIT_BAS):
        return "subit"
    if sans_degats or d["min"] >= 9.5:
        # Vie à fond sur toute la rafale (ou autour) : le jeu n'inflige peut-être pas de dégâts (Boxing). Une
        # rafale à vie pleine n'apprend rien.
        return "inconnu"
    # Une potion de soin dans la fenêtre dit que le joueur était en danger : au mieux un échange, jamais un enchaînement
    # sans réponse.
    return "donne" if d["perte"] <= SEUIL_DONNE and not d.get("soin") else "echange"


def sans_degats(temps: np.ndarray, vie: np.ndarray, t: float, fenetre: float = FENETRE_CONTEXTE) -> bool:
    """Vrai si la vie lue autour de t reste à fond (ou si trop peu de lectures : on ne conclut rien)."""
    m = (temps >= t - fenetre) & (temps <= t + fenetre) & ~np.isnan(vie)
    return True if m.sum() < 3 else bool(vie[m].min() >= 9.5)


def _fusion(fenetres: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(fenetres):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _contexte(path: str, lay: Layout, rafales: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Vie lue sur les images clés des seules fenêtres autour des rafales (pas tout le rush)."""
    ts, vs = [], []
    for a, b in _fusion([(max(0.0, (c["start"] + c["end"]) / 2 - FENETRE_CONTEXTE),
                          (c["start"] + c["end"]) / 2 + FENETRE_CONTEXTE) for c in rafales]):
        t, v = _lire_flux(path, a, b, lay, 0, cles_seules=True)
        ts.append(t)
        vs.append(v)
    if not ts:
        return np.zeros(0), np.zeros(0)
    return np.concatenate(ts), np.concatenate(vs)


def _courbe(t: np.ndarray, vie: np.ndarray) -> dict | None:
    """Courbe de vie nettoyée d'une rafale, gardée avec son résultat : le montage y cherche l'instant où le joueur cesse
    de perdre de la vie. {"t0": instant de la première mesure (s du rush), "fps", "v": vie en cœurs}."""
    if len(t) < 3 or np.isnan(vie).mean() > MAX_INVALIDES:
        return None
    v = _nettoyer(vie)
    return {"t0": round(float(t[0]), 3), "fps": FPS_VIE, "v": [round(float(x), 1) for x in v]}


def courbe_vie(path: str, c: dict, lay: Layout) -> dict | None:
    """Courbe de vie d'une rafale seule (résultats gardés avant que la courbe existe : on ne remesure que la vie)."""
    t, vie = _lire_flux(path, c["start"] - MARGE, c["end"] + MARGE_COURBE, lay, FPS_VIE)
    return _courbe(t, vie)


def mesure_vie(path: str, c: dict, lay: Layout, ctx_t: np.ndarray, ctx_v: np.ndarray) -> dict:
    t, vie = _lire_flux(path, c["start"] - MARGE, c["end"] + MARGE_COURBE, lay, FPS_VIE)
    dans = t < c["end"] + MARGE - 1e-6  # le verdict se lit sur la même fenêtre qu'avant ; la courbe, elle, va plus loin
    d = descripteurs(vie[dans])
    sd = sans_degats(ctx_t, ctx_v, (c["start"] + c["end"]) / 2)
    d["verdict"] = verdict_vie(d, sd)
    d["sans_degats"] = sd
    if d.get("valide"):
        d["courbe"] = _courbe(t, vie)  # None si la fenêtre élargie a trop de trous : noté, pour ne pas relire la vidéo à chaque montage
    return d


# =============================================================================================================
# Roulis de la caméra
# =============================================================================================================

def _lire_images(video: str, t0: float, t1: float) -> tuple[np.ndarray, float]:
    t0 = max(t0, 0.0)
    p = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{max(t1 - t0, 0.1):.3f}",
                        "-i", video, "-an", "-vf", f"fps={FPS_ROULIS},scale={W}:{H}:flags=area", "-f", "rawvideo",
                        "-pix_fmt", "bgr24", "-"], capture_output=True)
    a = np.frombuffer(p.stdout, np.uint8)
    n = len(a) // (W * H * 3)
    return a[: n * W * H * 3].reshape(n, H, W, 3), t0


def roulis(images: np.ndarray) -> list[tuple[float, int]]:
    """Pour chaque paire d'images successives : (angle en degrés, nombre d'inliers). (0, 0) si le suivi échoue."""
    gris = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in images]
    out: list[tuple[float, int]] = []
    for a, b in zip(gris[:-1], gris[1:]):
        p = cv2.goodFeaturesToTrack(a, 300, 0.01, 6, mask=_MASQUE)
        if p is None or len(p) < 12:
            out.append((0.0, 0))
            continue
        q, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p, None, winSize=(21, 21), maxLevel=3)
        st = st.ravel() == 1
        p, q = p[st].reshape(-1, 2), q[st].reshape(-1, 2)
        if len(p) < 12:
            out.append((0.0, 0))
            continue
        M, inl = cv2.estimateAffinePartial2D(p, q, method=cv2.RANSAC, ransacReprojThreshold=2.0)
        if M is None:
            out.append((0.0, 0))
            continue
        out.append((float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))), int(inl.sum())))
    return out


def impulsions(serie: list[tuple[float, int]], t0: float):
    """Instants (s) des coups reçus, masque des pas fiables et des pas aberrants, d'après le roulis."""
    th = np.array([s[0] for s in serie])
    ninl = np.array([s[1] for s in serie])
    ev = []
    for i in range(1, len(th) - 1):
        if ninl[i] < 10 or not (SEUIL_PIC <= abs(th[i]) <= PIC_MAX):
            continue
        # impulsion = saut franc dont le retour (pas suivant) est de signe opposé ; la queue de retour a le même
        # signe que le retour et ne doit pas être recomptée
        if th[i] > 0 and th[i + 1] < 1.0:
            ev.append(t0 + (i + 0.5) / FPS_ROULIS)
        elif th[i] < 0 and -2.5 < th[i - 1] < 3.0:
            ev.append(t0 + (i + 0.5) / FPS_ROULIS)
    return np.array(ev), ninl >= 10, np.abs(th) > PIC_MAX


def verdict_roulis(start: float, end: float, hits: list[float], serie: list[tuple[float, int]], t0: float) -> dict:
    """Verdict de la caméra pour une rafale. `hits` : instants des coups entendus."""
    if len(serie) < 3:
        return {"verdict": "inconnu", "coups": len(hits), "recus": 0, "impulsions": 0, "fiable": 0.0, "fenetre": None}
    ev, fiable, aberrant = impulsions(serie, t0)
    hs = [h for h in hits if start - 0.1 <= h <= end + 0.1]
    recu = [bool(((ev - h > -DECALAGE_AVANT) & (ev - h < DECALAGE_APRES)).any()) for h in hs]
    n_recu = sum(recu)
    bornes = np.concatenate([[start - 0.3], ev[(ev > start - 0.3) & (ev < end + 0.3)], [end + 0.3]])
    meilleur = None
    for a, b in zip(bornes[:-1], bornes[1:]):
        if b - a < TROU_MIN:
            continue
        dedans = [h for h in hs if a <= h <= b + DECALAGE_AVANT]  # le son suit son impulsion de 0,35 s au plus
        i0, i1 = max(int((a - t0) * FPS_ROULIS), 0), min(int((b - t0) * FPS_ROULIS), len(fiable))
        part = float(fiable[i0:i1].mean()) if i1 > i0 else 0.0
        ab = bool(aberrant[i0:i1].any()) if i1 > i0 else True
        if len(dedans) >= COUPS_MIN and part >= FIABLE_MIN and not ab:
            if meilleur is None or len(dedans) > meilleur["coups"]:
                meilleur = {"debut": round(float(a), 2), "fin": round(float(b), 2), "coups": len(dedans)}
    suivi = float(fiable.mean()) if len(fiable) else 0.0
    if meilleur:
        etiq = "donne"
    elif hs and n_recu / len(hs) >= PART_SUBIT and n_recu >= 4:
        etiq = "subit"
    elif suivi < 0.5:
        etiq = "inconnu"  # suivi trop mauvais pour dire quoi que ce soit
    else:
        etiq = "echange"
    return {"verdict": etiq, "coups": len(hs), "recus": n_recu, "impulsions": int(len(ev)),
            "fiable": round(suivi, 2), "fenetre": [meilleur["debut"], meilleur["fin"]] if meilleur else None}


def mesure_roulis(video: str, c: dict, marge: float = 1.0) -> dict:
    imgs, t0 = _lire_images(video, c["start"] - marge, c["end"] + marge)
    return verdict_roulis(c["start"], c["end"], [h[0] for h in c["hits"]], roulis(imgs), t0)


# =============================================================================================================
# Verdict combiné et règles de sélection
# =============================================================================================================

def _cle(c: dict) -> str:
    """Identifiant d'une rafale dans un rush : le même que celui du jury (`jury.key`), ses bornes au centième."""
    return f"{c['start']:.2f}-{c['end']:.2f}"


def _c(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def classer(vie: dict | None, cam: dict | None, hud: bool = True) -> dict:
    """Combine les deux mesures. « subit » de l'une ou l'autre l'emporte (veto). « donne » demande la vie qui tient
    ou une fenêtre calme à la caméra (si la vie perd plus de 1 cœur, la fenêtre calme ne suffit pas : échange).
    `hud` : la barre de vie a été trouvée, donc c'est bien le jeu attendu. Sans elle, une caméra calme ne prouve
    rien (un autre jeu ne penche pas la vue quand on est touché) : seul « subit » reste possible."""
    v = (vie or {}).get("verdict", "inconnu")
    r = (cam or {}).get("verdict", "inconnu")
    if "subit" in (v, r):
        label = "subit"
    elif v == "donne":
        label = "donne"
    elif r == "donne":
        label = "donne" if v == "inconnu" else "echange"
    elif "echange" in (v, r):
        label = "echange"
    else:
        label = "inconnu"
    part = None
    if cam and cam.get("coups", 0) >= COUPS_MIN and cam.get("fiable", 0) >= 0.5 and r != "inconnu":
        part = round(cam["recus"] / cam["coups"], 2)
    if label == "donne" and part is not None and part >= ECHANGE_PART_MAX:
        label = "echange"  # la vie tient, mais la caméra voit la moitié des coups ou plus reçus : pas un enchaînement net
    if not hud and label in ("donne", "echange"):
        label = "inconnu"
    cout = None
    if vie and vie.get("valide") and v != "inconnu":
        cout = round(vie["perte"], 1)
    parts = []
    if vie and vie.get("valide"):
        parts.append(f"vie {_c(vie['debut'])} → {_c(vie['fin'])} cœurs (creux {_c(vie['min'])})"
                     + (", potion de soin" if vie.get("soin") else ""))
    if part is not None:
        parts.append(f"{cam['recus']} coups reçus sur {cam['coups']} (caméra qui penche)")
    if label == "donne" and cam and cam.get("fenetre"):
        parts.append("enchaînement sans coup reçu de %s s à %s s" % tuple(_c(x) for x in cam["fenetre"]))
    return {"label": label, "confirme": v == "donne" and r == "donne", "cout": cout, "part_recue": part,
            "vie": _vie_resume(vie), "camera": cam, "fenetre": (cam or {}).get("fenetre"),
            "raison": ", ".join(parts) or "aucune mesure exploitable"}


def _vie_resume(vie: dict | None) -> dict | None:
    if not vie:
        return None
    return {k: (round(vie[k], 1) if isinstance(vie.get(k), float) else vie.get(k))
            for k in ("verdict", "valide", "debut", "fin", "min", "perte", "soin", "sans_degats", "courbe") if k in vie}


def inconnu(raison: str) -> dict:
    return {"label": "inconnu", "confirme": False, "cout": None, "part_recue": None, "vie": None, "camera": None,
            "fenetre": None, "raison": raison}


def a_confirmer(res: dict | None) -> bool:
    """Dernière rafale d'un combat que la mesure donne pour subie. C'est peut-être le kill qui clôt le duel gagné (la
    vie a chuté pendant le combat, mais l'adversaire est mort) : seul le jury, qui voit l'issue, peut le dire. Elle
    n'entre donc dans le montage que si le jury confirme un kill du joueur. `fermeture` est posé par
    `pvp.annotate` (dernière rafale de son combat)."""
    return bool(res) and res.get("label") == "subit" and bool(res.get("fermeture"))


def admissible(res: dict | None) -> bool:
    """Le combo peut-il passer à l'étape suivante (jury, classement) ? Jamais s'il est subi, sauf une fin de combat à
    confirmer ; un échange seulement s'il ne coûte presque rien : ni vie perdue, ni potion de soin, ni la moitié des
    coups reçus."""
    if not res:
        return True  # pas de mesure : on ne bloque rien
    if res.get("label") == "subit":
        return a_confirmer(res)
    if res.get("label") == "echange":
        if res.get("cout") is not None and res["cout"] > ECHANGE_COUT_MAX:
            return False
        if res.get("part_recue") is not None and res["part_recue"] >= ECHANGE_PART_MAX:
            return False
        if (res.get("vie") or {}).get("soin"):
            return False
    return True


def rang(res: dict | None) -> int:
    """Préférence : 0 donne confirmé par les deux mesures, 1 donne, 2 échange à faible coût ou fin de combat à
    confirmer, 3 le reste."""
    label = (res or {}).get("label")
    if label == "donne":
        return 0 if res.get("confirme") else 1
    return 2 if label == "echange" or a_confirmer(res) else 3


def courbe_de(res: dict | None) -> tuple[np.ndarray, np.ndarray] | None:
    """(instants dans le rush, vie en cœurs) de la courbe gardée avec le résultat d'une rafale ; None si absente."""
    c = ((res or {}).get("vie") or {}).get("courbe")
    try:
        v = np.asarray(c["v"], dtype=float)
        fps = float(c["fps"])
        t0 = float(c["t0"])
    except (TypeError, KeyError, ValueError):
        return None
    if len(v) < 3 or fps <= 0 or np.isnan(v).any():
        return None
    return t0 + np.arange(len(v)) / fps, v


def fenetre_sans_chute(res: dict | None, ref: float, perte_max: float = PERTE_MAX_PLAN) -> tuple[float, float] | None:
    """Plus large passage (début, fin dans le rush) autour de `ref` où la vie du joueur ne chute pas de plus de `perte_max`
    cœurs (du pic au creux qui le suit : un soin n'efface pas une perte). C'est ce qu'on peut montrer sans le voir encaisser.
    On étend d'abord vers la fin (le temps de voir le coup final), puis vers le début. None sans courbe de vie."""
    cv = courbe_de(res)
    if cv is None:
        return None
    t, v = cv
    i = int(np.argmin(np.abs(t - ref)))
    lo = hi = i
    pic = v[i]
    while hi + 1 < len(v) and pic - v[hi + 1] <= perte_max + 1e-9:
        hi += 1
        pic = max(pic, v[hi])
    creux = v[lo:hi + 1].min()
    while lo > 0 and v[lo - 1] - creux <= perte_max + 1e-9:
        lo -= 1
        creux = min(creux, v[lo])
    # Un bord arrêté par une chute est repoussé d'une mesure (début) ou d'une demi-mesure (fin) : l'image qui change de vie
    # tombe entre deux mesures, et une image de plus ou de moins d'un côté ou de l'autre montrerait déjà la chute.
    pas = float(t[1] - t[0])
    return float(t[lo] + (pas if lo > 0 else 0.0)), float(t[hi] - (0.5 * pas if hi < len(v) - 1 else 0.0))


def pourquoi(res: dict | None) -> str:
    """Raison d'écart d'une rafale non admissible, pour le montage et le résumé."""
    if not res or admissible(res):
        return ""
    if res["label"] == "subit":
        return f"Écarté : tu subissais ce combo ({res['raison']})."
    return f"Écarté : tu encaissais aussi dans cet échange ({res['raison']})."


def label(res: dict | None) -> str:
    """Le verdict en clair."""
    if not res:
        return "pas mesuré"
    nom = {"donne": "tu enchaînes", "subit": "tu subis", "echange": "échange", "inconnu": "non tranché"}.get(
        res.get("label"), "non tranché")
    return f"{nom} ({res.get('raison', 'aucune mesure exploitable')})"


def bilan(cands: list[tuple[str, dict]]) -> dict[str, int]:
    """Compte des rafales par issue du signal local : donne, echange (admis), ecartees (subies ou trop chères, y
    compris les fins de combat à confirmer), inconnu (non tranché ou non mesuré)."""
    n = {"donne": 0, "echange": 0, "ecartees": 0, "inconnu": 0, "subies": 0}
    for _, c in cands:
        d = c.get("duel")
        if not admissible(d) or a_confirmer(d):
            n["ecartees"] += 1
            n["subies"] += (d or {}).get("label") == "subit"
        elif d and d["label"] in ("donne", "echange"):
            n[d["label"]] += 1
        else:
            n["inconnu"] += 1
    return n


# =============================================================================================================
# Calcul et cache
# =============================================================================================================

def rafales_de(signals: dict | None) -> dict:
    """Résultats gardés dans les signaux d'un rush : {clé de rafale: résultat}. Vide si la version des règles de
    lecture a changé : un ancien format n'est jamais relu."""
    d = (signals or {}).get("duel")
    if not isinstance(d, dict) or d.get("v") != VERSION:
        return {}
    return {k: r for k, r in (d.get("rafales") or {}).items() if isinstance(r, dict) and r.get("label") in LABELS}


def _store(signals: dict | None) -> dict:
    """Bloc `duel` des signaux d'un rush, nettoyé : vide si la version a changé, sans résultat mal formé."""
    d = (signals or {}).get("duel")
    if not isinstance(d, dict) or d.get("v") != VERSION:
        return {"v": VERSION, "rafales": {}}
    return {**d, "rafales": rafales_de(signals)}


def cached(project_id: str, media_id: str) -> dict:
    """Résultats déjà calculés pour ce rush : {clé de rafale: résultat}."""
    return rafales_de(storage.load_signals(project_id, media_id))


def plages_subies(project_id: str, media_id: str) -> list[tuple[float, float]]:
    """Plages (début, fin dans le rush) des rafales que la mesure donne pour subies, d'après les résultats gardés."""
    out = []
    for k, r in cached(project_id, media_id).items():
        if r.get("label") == "subit":
            try:
                a, b = (float(x) for x in k.split("-"))
            except ValueError:
                continue
            out.append((a, b))
    return sorted(out)


def zone_vie(project_id: str, m) -> tuple[float, float, float, float] | None:
    """Zone de la barre de vie (fractions de l'image) pour le jury ; None si on a cherché sans la trouver.
    Sans analyse préalable : estimation pour l'interface de base du jeu (échelle ~ hauteur / 540)."""
    d = (storage.load_signals(project_id, m.id) or {}).get("duel") or {}
    if d.get("v") == VERSION and "interface" in d:
        return Layout(**d["interface"]).zone_jury() if d["interface"] else None
    w, h = getattr(m, "width", 0) or 0, getattr(m, "height", 0) or 0
    if w > 0 and h > 0:
        s = max(1.0, round(h / 540))
        lay = Layout(w, h, s, w / 2 - 91 * s, h - 39 * s, 0.0)
        return lay.zone_jury()
    return None


def analyse(project_id: str, media: list, cands: list[tuple[str, dict]], ctx=None, workers: int = 3,
            seulement: set | None = None) -> dict[tuple[str, str], dict]:
    """Mesure les rafales pas encore vues et renvoie les résultats connus : {(média, clé): résultat}.

    `seulement` : clés (média, clé de rafale) à mesurer ; les autres ne sont pas lues (un edit qui ne vise que
    quelques rafales ne paie pas la mesure de tout le rush) mais gardent leur résultat s'il existe déjà.
    Les images lues sont celles des rafales (et quelques images clés autour). Toute erreur de lecture donne
    « inconnu » pour la rafale concernée, jamais « donne », et n'est pas gardée : la prochaine analyse réessaie."""
    out: dict[tuple[str, str], dict] = {}
    for m in media:
        mine = [c for mid, c in cands if mid == m.id]
        if not mine:
            continue
        store = _store(storage.load_signals(project_id, m.id))
        have = store["rafales"]

        def voulu(c: dict, mid: str = m.id) -> bool:
            return seulement is None or (mid, _cle(c)) in seulement
        todo = [c for c in mine if _cle(c) not in have and voulu(c)]
        sure: dict[str, dict] = {}
        if todo:
            fresh = _mesurer(m, todo, store, ctx, workers)
            sure = {k: r for k, r in fresh.items() if not r.get("echec")}  # un échec ponctuel n'est pas écrit en dur
            have.update(fresh)
        # Résultats gardés avant que la courbe de vie existe : on la relit pour ceux que le montage peut retenir.
        courbes = _completer(m, [c for c in mine if _cle(c) in have and voulu(c) and _sans_courbe(have[_cle(c)], c)],
                             store, ctx, workers)
        for k, courbe in courbes.items():
            have[k]["vie"]["courbe"] = courbe
        if sure or courbes or (todo and "interface" in store):
            sig = storage.load_signals(project_id, m.id) or {}  # relu : une autre analyse a pu écrire entre-temps
            now = _store(sig)
            if "interface" in store:
                now["interface"] = store["interface"]
            now.setdefault("rafales", {}).update(sure)
            for k, courbe in courbes.items():
                vie = (now["rafales"].get(k) or {}).get("vie")
                if isinstance(vie, dict):
                    vie["courbe"] = courbe
            sig["duel"] = now
            storage.save_signals(project_id, m.id, sig)
        for c in mine:
            if _cle(c) in have:
                out[(m.id, _cle(c))] = have[_cle(c)]
    return out


def _sans_courbe(res: dict, c: dict) -> bool:
    """Résultat lu sans courbe de vie, pour une rafale que le montage peut retenir (pas une rafale subie en plein combat)."""
    vie = res.get("vie")
    if not isinstance(vie, dict) or not vie.get("valide") or "courbe" in vie or res.get("echec"):
        return False
    return res.get("label") != "subit" or bool(c.get("closing"))


def _completer(m, rafales: list[dict], store: dict, ctx, workers: int) -> dict[str, dict | None]:
    """Relit la seule barre de vie des rafales gardées sans courbe. Une panne de lecture laisse la rafale sans courbe : le
    montage la traite alors prudemment, et la prochaine analyse réessaie. Une courbe lue mais inexploitable est notée `None`."""
    if not rafales or not store.get("interface") or not getattr(m, "has_video", True):
        return {}
    lay = Layout(**store["interface"])
    if ctx:
        ctx.step(f"{m.filename} : courbe de vie de {len(rafales)} rafales")

    def un(c: dict) -> tuple[str, bool, dict | None]:
        if ctx:
            ctx.check()
        try:
            return _cle(c), True, courbe_vie(m.path, c, lay)
        except Exception as e:
            log.warning("Courbe de vie illisible (%s) : %s", _cle(c), e)
            return _cle(c), False, None

    out: dict[str, dict | None] = {}
    ex = ThreadPoolExecutor(max(1, workers))
    try:
        for f in as_completed([ex.submit(un, c) for c in rafales]):
            k, lue, courbe = f.result()
            if lue:  # lue mais inexploitable (trop de trous) : `None` est gardé et la prochaine analyse ne relit pas ; une panne réessaie
                out[k] = courbe
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    return out


def _mesurer(m, todo: list[dict], store: dict, ctx, workers: int) -> dict[str, dict]:
    """Mesure `todo`. `store["interface"]` n'est renseigné que si la barre de vie a été cherchée pour de bon (trouvée ou
    absente) ; une panne de lecture le laisse vide et marque les résultats `echec`, pour ne rien graver."""
    if not m.has_video:
        return {_cle(c): inconnu("pas de vidéo") for c in todo}
    n = len(todo)
    if ctx:
        ctx.step(f"{m.filename} : qui porte les coups ? ({n} rafales, vie et caméra)")
    panne = False
    try:
        if "interface" not in store:
            lay = detecter_layout(m.path, m.width, m.height, m.duration)
            store["interface"] = asdict(lay) if lay else None
        lay = Layout(**store["interface"]) if store["interface"] else None
    except Exception as e:  # le montage se fait alors sans ce signal, et la panne n'est pas gardée
        log.warning("Barre de vie non cherchée pour %s : %s", m.filename, e)
        store.pop("interface", None)
        lay, panne = None, True
    cam_src = m.proxy if m.proxy and os.path.exists(m.proxy) else m.path
    ctx_t, ctx_v = np.zeros(0), np.zeros(0)
    if lay:
        try:
            ctx_t, ctx_v = _contexte(m.path, lay, todo)
        except Exception as e:
            log.warning("Contexte de vie illisible pour %s : %s", m.filename, e)
            panne = True

    def un(c: dict) -> tuple[str, dict]:
        if ctx:
            ctx.check()
        vie = cam = None
        echec = panne
        try:
            vie = mesure_vie(m.path, c, lay, ctx_t, ctx_v) if lay else None
        except Exception as e:
            log.warning("Vie illisible (%s) : %s", _cle(c), e)
            echec = True
        try:
            cam = mesure_roulis(cam_src, c)
        except Exception as e:
            log.warning("Roulis illisible (%s) : %s", _cle(c), e)
            echec = True
        res = classer(vie, cam, hud=lay is not None)
        if not lay:
            res["raison"] = "barre de vie introuvable" + ("" if res["raison"].startswith("aucune") else f" ; {res['raison']}")
        if echec:
            res["echec"] = True
        return _cle(c), res

    out: dict[str, dict] = {}
    ex = ThreadPoolExecutor(max(1, workers))
    try:
        futs = [ex.submit(un, c) for c in todo]
        for f in as_completed(futs):  # une étape par rafale : la tâche (et le délai du chat) voit que ça avance
            k, res = f.result()
            out[k] = res
            if ctx:
                ctx.step(f"{m.filename} : qui porte les coups ? ({len(out)}/{n} rafales mesurées)")
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    return out
