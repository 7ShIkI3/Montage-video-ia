"""Modèle de données du projet. La timeline est un simple document JSON."""
from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:10]


Style = Literal["youtube", "short", "vlog", "podcast"]
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

    @property
    def duration(self) -> float:
        return max(0.0, self.src_out - self.src_in)


class MusicSettings(BaseModel):
    track: str | None = None  # chemin d'un fichier de la bibliothèque ou du projet
    volume: float = 0.18
    ducking: bool = True


class EditSettings(BaseModel):
    style: Style = "youtube"
    target_duration: float | None = None  # secondes ; None = automatique
    format: Format = "16:9"
    captions: CaptionStyle = "karaoke"
    remove_fillers: bool = True
    remove_silences: bool = True
    punch_zooms: bool = True
    music: bool = False
    instructions: str = ""  # consignes libres données au réalisateur IA


class Timeline(BaseModel):
    clips: list[Clip] = Field(default_factory=list)
    format: Format = "16:9"
    captions: CaptionStyle = "karaoke"
    music: MusicSettings = Field(default_factory=MusicSettings)
    title: str = ""
    summary: str = ""
    director: str = ""  # "claude" ou "heuristique"
    rejected: list[Clip] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)


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
