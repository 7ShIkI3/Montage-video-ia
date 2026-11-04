"""Modèle de données du projet. La timeline est un simple document JSON."""
from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:10]


Style = Literal["youtube", "short", "vlog", "podcast", "pvp"]
Format = Literal["16:9", "9:16", "1:1"]
CaptionStyle = Literal["karaoke", "classique", "aucun"]


class Media(BaseModel):
    id: str = Field(default_factory=lambda: new_id("m_"))
    filename: str
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 30.0
    has_audio: bool = True
    has_video: bool = True
    proxy: str | None = None
    analyzed: bool = False


class Word(BaseModel):
    start: float
    end: float
    text: str
    prob: float = 1.0
    filler: bool = False


class Transcript(BaseModel):
    media_id: str
    language: str | None = None
    words: list[Word] = Field(default_factory=list)


class Segment(BaseModel):
    """Un moment candidat repéré par l'analyse (phrase, plan ou passage d'action)."""

    id: str
    media_id: str
    start: float
    end: float
    text: str = ""
    kind: Literal["speech", "action"] = "speech"
    features: dict[str, float] = Field(default_factory=dict)
    score: float = 0.0
    duplicate_of: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


class Region(BaseModel):
    """Zone de l'image, en coordonnées normalisées (0..1) du rush source."""

    x: float
    y: float
    w: float
    h: float

    def clamp(self) -> "Region":
        x, y = min(max(self.x, 0.0), 0.99), min(max(self.y, 0.0), 0.99)
        return Region(x=x, y=y, w=min(max(self.w, 0.01), 1 - x), h=min(max(self.h, 0.01), 1 - y))


class Mask(Region):
    """Zone traitée sur un plan : flou (visage, plaque…), pixelisé, cache noir ou cadre de mise en avant."""

    kind: Literal["flou", "pixel", "noir", "cadre"] = "flou"
    follow_dx: float | None = None  # si défini, la zone suit le visage (x = position du visage + décalage)


class Keyframe(BaseModel):
    """Image clé d'un plan : à l'instant t (secondes depuis le début du plan, sur la timeline), le cadre vaut
    ce zoom et est centré sur (x, y) du rush (0..1). Entre deux images clés, le cadre glisse."""
    t: float = 0.0
    zoom: float = Field(1.0, ge=1.0, le=4.0)
    x: float = Field(0.5, ge=0.0, le=1.0)
    y: float = Field(0.5, ge=0.0, le=1.0)
    ease: Literal["lineaire", "doux"] = "doux"


class Clip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("c_"))
    media_id: str
    src_in: float
    src_out: float
    segment_id: str | None = None
    score: float = 0.0
    reason: str = ""
    zoom: float = 1.0
    volume: float = 1.0
    speed: float = 1.0  # 0.25 à 4 : ralenti / accéléré (vitesse au début du plan si rampe)
    speed_end: float | None = None  # rampe de vitesse : vitesse à la fin du plan (variation progressive)
    transition: str = "aucune"  # transition d'entrée (voir render/effects.py)
    transition_duration: float = 0.35
    effects: list[str] = Field(default_factory=list)  # effets vidéo
    audio_effects: list[str] = Field(default_factory=list)  # effets audio
    focus_x: float | None = None  # point de cadrage imposé (0..1 du rush), prioritaire sur le suivi du visage
    focus_y: float | None = None
    masks: list[Mask] = Field(default_factory=list)
    keyframes: list[Keyframe] = Field(default_factory=list)  # zoom et cadrage animés

    @property
    def source_duration(self) -> float:
        return max(0.0, self.src_out - self.src_in)

    @property
    def ramp(self) -> tuple[float, float] | None:
        """(vitesse de début, vitesse de fin) quand le plan a une rampe de vitesse."""
        if self.speed_end is None:
            return None
        s0, s1 = max(0.25, min(4.0, self.speed)), max(0.25, min(4.0, self.speed_end))
        return None if abs(s1 - s0) < 1e-3 else (s0, s1)

    @property
    def duration(self) -> float:
        """Durée sur la timeline (tient compte de la vitesse, et de la rampe : vitesse moyenne)."""
        r = self.ramp
        if r:
            return 2 * self.source_duration / (r[0] + r[1])
        return self.source_duration / max(0.05, self.speed)

    def local_to_src(self, t: float) -> float:
        """Instant t du plan (timeline) -> secondes de rush consommées depuis src_in."""
        r = self.ramp
        if not r:
            return t * max(0.05, self.speed)
        s0, s1 = r
        return s0 * t + (s1 - s0) * t * t / (2 * self.duration)

    def src_to_local(self, tau: float) -> float:
        """Secondes de rush depuis src_in -> instant du plan sur la timeline."""
        r = self.ramp
        if not r:
            return tau / max(0.05, self.speed)
        s0, s1 = r
        a = (s1 - s0) / (2 * self.duration)
        disc = max(0.0, s0 * s0 + 4 * a * tau)
        return (-s0 + disc ** 0.5) / (2 * a)

    def speed_at(self, t: float) -> float:
        r = self.ramp
        if not r:
            return self.speed
        return r[0] + (r[1] - r[0]) * max(0.0, min(1.0, t / max(1e-6, self.duration)))


class TextItem(BaseModel):
    """Titre, bandeau ou texte incrusté sur la piste Textes."""

    id: str = Field(default_factory=lambda: new_id("t_"))
    start: float
    duration: float = 2.5
    text: str
    style: Literal["titre", "bandeau", "legende", "gros_texte", "emoji", "texte_3d"] = "titre"
    position: Literal["haut", "centre", "bas"] = "centre"


class Comment(BaseModel):
    """Commentaire du monteur posé sur un moment précis, que l'IA lit et applique.

    Il est ancré sur le rush (média + instant source) et non sur la timeline : il reste au bon
    endroit quand l'IA refait le montage."""

    id: str = Field(default_factory=lambda: new_id("n_"))
    media_id: str | None = None  # None = commentaire général sur tout le montage
    src_t: float = 0.0
    region: Region | None = None  # zone sélectionnée sur l'image (« floute ça », « zoom sur lui »…)
    text: str
    status: Literal["ouvert", "applique", "incompris"] = "ouvert"
    reply: str = ""
    created_at: float = Field(default_factory=time.time)


class SfxItem(BaseModel):
    """Bruitage placé sur la piste Effets sonores."""

    id: str = Field(default_factory=lambda: new_id("s_"))
    start: float
    kind: str = "whoosh"
    volume: float = 0.8


class ImpactItem(BaseModel):
    """Impact sur la piste Impacts : secousse 3D, zoom choc, flash, étincelles, onde de choc."""

    id: str = Field(default_factory=lambda: new_id("i_"))
    start: float
    kind: Literal["secousse", "zoom", "flash", "flash_rouge", "etincelles", "onde"] = "secousse"
    intensity: float = 1.0  # 0.2 à 2.5
    x: float = 0.5  # point d'origine à l'écran (étincelles, onde)
    y: float = 0.5
    reason: str = ""


class ImageItem(BaseModel):
    """Image posée sur la vidéo (piste Images) : logo, filigrane, sticker, photo."""

    id: str = Field(default_factory=lambda: new_id("g_"))
    asset: str  # nom du fichier dans le dossier assets du projet
    start: float = 0.0
    duration: float | None = None  # None = toute la vidéo (filigrane)
    x: float = 0.9  # centre de l'image, en fraction de la largeur
    y: float = 0.1
    scale: float = 0.14  # largeur de l'image en fraction de la largeur de la vidéo
    opacity: float = 0.85
    anim: Literal["aucune", "fondu", "pop"] = "fondu"


class OverlayItem(BaseModel):
    """Plan de coupe (B-roll) sur la piste vidéo 2, plein cadre ou en incrustation."""

    id: str = Field(default_factory=lambda: new_id("o_"))
    media_id: str
    src_in: float
    src_out: float
    start: float
    mode: Literal["plein", "incrustation", "partage"] = "plein"  # partage : écran partagé avec le plan principal
    reason: str = ""
    focus_x: float | None = None  # cadrage du plan de coupe en écran partagé (0..1 du rush, centre par défaut)
    chroma: Literal["aucun", "vert", "bleu"] = "aucun"  # incrustation sur fond vert / bleu
    corner: Literal["haut_droite", "haut_gauche", "bas_droite", "bas_gauche"] = "haut_droite"

    @property
    def duration(self) -> float:
        return max(0.0, self.src_out - self.src_in)


class MusicSettings(BaseModel):
    track: str | None = None  # chemin d'un fichier de la bibliothèque ou du projet
    volume: float = 0.18
    ducking: bool = True
    start: float = 0.0  # secondes sautées au début du morceau (cale ses temps sur le début du montage)


class EditSettings(BaseModel):
    style: Style = "youtube"
    target_duration: float | None = None  # secondes ; None = automatique
    format: Format = "16:9"
    captions: CaptionStyle = "karaoke"
    remove_fillers: bool = True
    remove_silences: bool = True
    punch_zooms: bool = True
    music: bool = False
    auto_effects: bool = True  # l'IA place transitions, effets, bruitages, titres et B-roll
    instructions: str = ""  # consignes libres données au réalisateur IA


class AudioItem(BaseModel):
    """Son posé sur une piste audio : voix off enregistrée, musique ou son importé."""
    id: str = Field(default_factory=lambda: new_id("a_"))
    asset: str  # nom du fichier dans le dossier audio du projet
    start: float = 0.0  # position sur la timeline
    src_in: float = 0.0
    src_out: float  # fin dans le fichier
    lane: int = Field(0, ge=0, le=3)  # piste audio 1 à 4
    kind: Literal["voix", "musique", "son"] = "voix"
    volume: float = Field(1.0, ge=0.0, le=2.0)
    fade_in: float = 0.05
    fade_out: float = 0.15
    label: str = ""
    dub: str | None = None  # langue du doublage IA qui a créé ce son (remplacé quand on double à nouveau)

    @property
    def duration(self) -> float:
        return max(0.0, self.src_out - self.src_in)


class QualityIssue(BaseModel):
    """Un point du contrôle qualité : règle de réalisation non tenue ou mesure technique hors cible."""
    check: str  # accroche, moment_fort, plan_long, texte, lecture, flashs, duree, bruitage, musique, son, image
    level: Literal["erreur", "alerte", "ok"] = "alerte"
    message: str
    t: float | None = None  # instant concerné sur la timeline
    fix: str | None = None  # correction automatique possible (clé), sinon None
    target: str | None = None  # id de l'élément visé (plan, texte, bruitage…)


class QualityReport(BaseModel):
    score: int = 100  # 0..100
    issues: list[QualityIssue] = Field(default_factory=list)
    measured: dict = Field(default_factory=dict)  # mesures du rendu : loudness, crête, flashs…
    rendered: bool = False  # True si le rapport inclut le contrôle de la vidéo finie
    fixed: list[str] = Field(default_factory=list)  # corrections appliquées automatiquement
    at: float = Field(default_factory=time.time)


class ReviewRemark(BaseModel):
    t: float | None = None
    problem: str
    fix: str = ""


class Review(BaseModel):
    """Note du relecteur indépendant (un second avis qui n'a pas vu la construction du montage)."""
    score: float = 0.0  # 0..10
    verdict: str = ""
    remarks: list[ReviewRemark] = Field(default_factory=list)
    reviewer: Literal["claude", "local"] = "local"
    rounds: int = 1
    history: list[float] = Field(default_factory=list)  # note à chaque tour
    at: float = Field(default_factory=time.time)


class Timeline(BaseModel):
    clips: list[Clip] = Field(default_factory=list)
    format: Format = "16:9"
    captions: CaptionStyle = "karaoke"
    music: MusicSettings = Field(default_factory=MusicSettings)
    title: str = ""
    summary: str = ""
    director: str = ""  # "claude" ou "heuristique"
    rejected: list[Clip] = Field(default_factory=list)
    look: str = "aucun"  # étalonnage global appliqué à toute la vidéo
    texts: list[TextItem] = Field(default_factory=list)
    sfx: list[SfxItem] = Field(default_factory=list)
    overlays: list[OverlayItem] = Field(default_factory=list)
    impacts: list[ImpactItem] = Field(default_factory=list)
    images: list[ImageItem] = Field(default_factory=list)
    audio: list[AudioItem] = Field(default_factory=list)  # pistes audio : voix off, sons, musiques en plus
    caption_language: str | None = None  # langue des sous-titres traduits (None = langue parlée)
    lut: str | None = None  # LUT d'étalonnage : "integre:<nom>" ou "projet:<fichier.cube>"
    lut_strength: float = Field(1.0, ge=0.0, le=1.0)
    background: str | None = None  # image de fond du détourage IA (fichier du dossier images du projet)
    dub_language: str | None = None  # langue du doublage IA en place (None = voix d'origine)
    quality: QualityReport | None = None  # dernier contrôle qualité (plan, puis vidéo finie)
    review: Review | None = None  # note du relecteur indépendant
    comments: list[Comment] = Field(default_factory=list)
    reframe: bool = True  # recadrage automatique sur le visage quand le format est plus étroit que la source

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)


class Version(BaseModel):
    """Une version du montage : le projet en garde plusieurs (montage principal, shorts, essais)."""

    id: str = Field(default_factory=lambda: new_id("v_"))
    name: str = "Montage principal"
    created_at: float = Field(default_factory=time.time)
    timeline: Timeline | None = None  # None pour la version active (sa timeline est Project.timeline)
    score: float | None = None  # score viral 0-100 pour les shorts automatiques
    note: str = ""


class Project(BaseModel):
    id: str = Field(default_factory=lambda: new_id("p_"))
    name: str = "Nouveau projet"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    media: list[Media] = Field(default_factory=list)
    settings: EditSettings = Field(default_factory=EditSettings)
    timeline: Timeline = Field(default_factory=Timeline)
    warnings: list[str] = Field(default_factory=list)
    exports: list[str] = Field(default_factory=list)
    publish_kit: dict | None = None  # titres, description, chapitres, hashtags, miniature
    versions: list[Version] = Field(default_factory=lambda: [Version()])
    active_version: str | None = None

    def ensure_versions(self) -> Version:
        """Version active (créée si besoin pour les anciens projets)."""
        if not self.versions:
            self.versions = [Version()]
        if not self.active_version or not any(v.id == self.active_version for v in self.versions):
            self.active_version = self.versions[0].id
        return next(v for v in self.versions if v.id == self.active_version)

    def media_by_id(self, media_id: str) -> Media:
        for m in self.media:
            if m.id == media_id:
                return m
        raise KeyError(media_id)


class CaptionLine(BaseModel):
    start: float
    end: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)
