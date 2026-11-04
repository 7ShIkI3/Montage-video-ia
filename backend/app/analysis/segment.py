"""Détourage des personnes sans fond vert (segmentation « selfie » de Google, modèle LiteRT de 250 Ko).

Le modèle est téléchargé une fois dans DATA_DIR/models (ou pris dans MONTEUR_SEGMENTER_MODEL).
Il tourne sur le processeur (environ 5 ms par image) grâce à `ai-edge-litert`.
"""
from __future__ import annotations

import logging
import os
import threading
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from .. import config

log = logging.getLogger(__name__)

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/"
             "float16/latest/selfie_segmenter.tflite")
_lock = threading.Lock()


class SegmenterUnavailable(RuntimeError):
    pass


def model_path() -> Path:
    env = os.environ.get("MONTEUR_SEGMENTER_MODEL")
    if env:
        return Path(env)
    p = config.DATA_DIR / "models" / "selfie_segmenter.tflite"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        try:
            log.info("Téléchargement du modèle de détourage…")
            urllib.request.urlretrieve(MODEL_URL, tmp)
            tmp.replace(p)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise SegmenterUnavailable(f"Modèle de détourage introuvable et téléchargement impossible ({e}). "
                                       f"Place selfie_segmenter.tflite dans {p.parent}.") from e
    return p


class Segmenter:
    """Masque des personnes (0..1) pour chaque image, lissé dans le temps pour éviter le scintillement."""

    def __init__(self):
        try:
            from ai_edge_litert.interpreter import Interpreter
        except ImportError as e:
            raise SegmenterUnavailable("Le détourage IA a besoin du paquet ai-edge-litert "
                                       "(pip install ai-edge-litert).") from e
        with _lock:
            self.it = Interpreter(model_path=str(model_path()), num_threads=max(1, (os.cpu_count() or 2) // 2))
        self.it.allocate_tensors()
        self.inp = self.it.get_input_details()[0]
        self.out = self.it.get_output_details()[0]
        self.h, self.w = int(self.inp["shape"][1]), int(self.inp["shape"][2])
        self.prev: np.ndarray | None = None

    def reset(self) -> None:
        self.prev = None

    def mask(self, frame_bgr: np.ndarray) -> np.ndarray:
        H, W = frame_bgr.shape[:2]
        x = cv2.resize(frame_bgr, (self.w, self.h), interpolation=cv2.INTER_AREA)
        x = cv2.cvtColor(x, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        self.it.set_tensor(self.inp["index"], x[None])
        self.it.invoke()
        m = self.it.get_tensor(self.out["index"])[0, ..., 0].astype(np.float32)
        if self.prev is not None and self.prev.shape == m.shape:
            m = 0.6 * m + 0.4 * self.prev  # lissage temporel léger
        self.prev = m
        # Contour net mais doux : seuil adouci puis flou léger à la taille de l'image.
        m = np.clip((m - 0.35) / 0.3, 0, 1)
        m = cv2.resize(m, (W, H), interpolation=cv2.INTER_LINEAR)
        k = max(3, (min(W, H) // 180) | 1)
        return cv2.GaussianBlur(m, (k, k), 0)


def composite(frame: np.ndarray, mask: np.ndarray, kind: str, bg_image: np.ndarray | None = None) -> np.ndarray:
    """Remplace le fond derrière la personne : flou, noir, blanc, studio (dégradé) ou image."""
    H, W = frame.shape[:2]
    if kind == "detourage_flou":
        small = cv2.resize(frame, (max(2, W // 8), max(2, H // 8)), interpolation=cv2.INTER_AREA)
        bg = cv2.resize(cv2.GaussianBlur(small, (0, 0), 3), (W, H), interpolation=cv2.INTER_LINEAR)
    elif kind == "detourage_noir":
        bg = np.zeros_like(frame)
    elif kind == "detourage_blanc":
        bg = np.full_like(frame, 245)
    elif kind == "detourage_image" and bg_image is not None:
        bg = bg_image
    else:  # studio : dégradé sombre violet, vignetté
        y = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
        top, bottom = np.array([70, 40, 52], np.float32), np.array([20, 12, 16], np.float32)
        bg = np.broadcast_to(top * (1 - y) + bottom * y, (H, W, 3)).astype(np.uint8)
    a = mask[..., None]
    return (frame.astype(np.float32) * a + bg.astype(np.float32) * (1 - a)).astype(np.uint8)


def cover_image(path: Path, W: int, H: int) -> np.ndarray | None:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return None
    s = max(W / img.shape[1], H / img.shape[0])
    img = cv2.resize(img, (int(np.ceil(img.shape[1] * s)), int(np.ceil(img.shape[0] * s))), interpolation=cv2.INTER_AREA)
    y0, x0 = (img.shape[0] - H) // 2, (img.shape[1] - W) // 2
    return np.ascontiguousarray(img[y0:y0 + H, x0:x0 + W])
