"""Sauvegardes automatiques du projet : copies datées, rotation, repli sur la dernière en cas de JSON corrompu,
restauration par l'API."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app
from app.models import Clip, Project

client = TestClient(app)


def _project(n_clips: int = 1) -> Project:
    p = Project(name="Sauvegardes")
    p.timeline.clips = [Clip(media_id="m", src_in=0, src_out=2.0) for _ in range(n_clips)]
    return storage.save_project(p)


def _age_backups(pid: str, seconds: float) -> None:
    """Vieillit toutes les sauvegardes (la limite d'une toutes les 5 minutes se lit sur la date du fichier)."""
    for f in storage._backup_files(pid):
        t = f.stat().st_mtime - seconds
        os.utime(f, (t, t))


def _names(pid: str) -> list[str]:
    return [f.name for f in storage._backup_files(pid)]


def test_first_save_makes_a_backup_then_at_most_one_per_five_minutes():
    p = _project()
    assert len(_names(p.id)) == 1
    for _ in range(5):
        storage.save_project(p)
    assert len(_names(p.id)) == 1  # saves rapprochées : pas de nouvelle copie
    _age_backups(p.id, storage.BACKUP_INTERVAL + 1)
    storage.save_project(p)
    assert len(_names(p.id)) == 2


def test_only_the_ten_latest_backups_are_kept():
    p = _project()
    for k in range(14):
        _age_backups(p.id, storage.BACKUP_INTERVAL + 1)
        p.timeline.clips.append(Clip(media_id="m", src_in=0, src_out=1.0))
        storage.save_project(p)
        time.sleep(0.002)  # noms datés au milliseconde
    names = _names(p.id)
    assert len(names) == storage.BACKUP_KEEP == 10
    assert names == sorted(names, reverse=True)
    # la plus récente contient le dernier état, les plus anciennes ont été supprimées
    assert storage.list_backups(p.id)[0]["clips"] == len(p.timeline.clips)
    assert min(b["clips"] for b in storage.list_backups(p.id)) == len(p.timeline.clips) - 9


def test_backup_listing_gives_name_date_clips_and_duration():
    p = _project(3)
    (b,) = storage.list_backups(p.id)
    assert b["clips"] == 3 and b["duration"] == pytest.approx(6.0)
    assert b["name"].startswith("project-") and abs(b["at"] - time.time()) < 60


def test_corrupt_project_json_falls_back_on_the_latest_backup(caplog):
    p = _project(2)
    path = storage.project_dir(p.id) / "project.json"
    path.write_text('{"id": "' + p.id + '", "timeline": {"clips": [', encoding="utf-8")  # écriture coupée net
    with caplog.at_level("WARNING"):
        back = storage.load_project(p.id)
    assert back.id == p.id and len(back.timeline.clips) == 2
    assert any("illisible" in r.message and p.id in r.message for r in caplog.records)
    assert (path.parent / "project.corrupt.json").exists()  # le fichier abîmé est gardé
    assert [x.id for x in storage.list_projects() if x.id == p.id] == [p.id]


def test_corrupt_project_without_backup_still_fails():
    p = _project()
    for f in storage._backup_files(p.id):
        f.unlink()
    (storage.project_dir(p.id) / "project.json").write_text("{pas du json", encoding="utf-8")
    with pytest.raises(ValueError):
        storage.load_project(p.id)


def test_a_broken_latest_backup_is_skipped():
    p = _project(2)
    _age_backups(p.id, storage.BACKUP_INTERVAL + 1)
    storage.save_project(p)  # deuxième sauvegarde, la plus récente
    storage._backup_files(p.id)[0].write_text("{abîmé", encoding="utf-8")
    (storage.project_dir(p.id) / "project.json").write_text("{abîmé", encoding="utf-8")
    assert len(storage.load_project(p.id).timeline.clips) == 2  # repli sur l'avant-dernière


# --- Routes -----------------------------------------------------------------------------------------

def test_list_route():
    p = _project(2)
    r = client.get(f"/api/projects/{p.id}/backups")
    assert r.status_code == 200
    (b,) = r.json()
    assert b["clips"] == 2 and set(b) == {"name", "at", "clips", "duration"}
    assert client.get("/api/projects/p_inconnu/backups").status_code == 404


def test_restore_route_puts_the_backup_back_after_keeping_the_current_state():
    p = _project(1)
    first = _names(p.id)[0]
    _age_backups(p.id, storage.BACKUP_INTERVAL + 1)
    p.timeline.clips += [Clip(media_id="m", src_in=0, src_out=2.0) for _ in range(3)]
    storage.save_project(p)
    assert len(storage.load_project(p.id).timeline.clips) == 4

    r = client.post(f"/api/projects/{p.id}/backups/{first}/restore")  # le verrou du projet est pris par la requête
    assert r.status_code == 200, r.text
    assert len(storage.load_project(p.id).timeline.clips) == 1
    # l'état d'avant la restauration est devenu une sauvegarde
    assert [b["clips"] for b in storage.list_backups(p.id)] == [4, 4, 1]  # la plus récente = état d'avant la restauration


def test_restore_refuses_path_traversal_and_unknown_names():
    p = _project()
    other = _project()
    for name in ("..", "../project.json", "..%2Fproject.json", "%2e%2e", "project.json",
                 "project-20200101-000000-000.json/../../x", "project-20200101-000000-000.json\\..\\x"):
        r = client.post(f"/api/projects/{p.id}/backups/{name}/restore")
        assert r.status_code in (400, 404), (name, r.status_code)
    with pytest.raises(ValueError):
        storage.backup_path(p.id, "../" + other.id + "/project.json")
    with pytest.raises(ValueError):
        storage.restore_backup(p.id, "..\\project.json")
    r = client.post(f"/api/projects/{p.id}/backups/project-20200101-000000-000.json/restore")
    assert r.status_code == 404
    assert client.post(f"/api/projects/p_inconnu/backups/{_names(p.id)[0]}/restore").status_code == 404
    assert len(storage.load_project(p.id).timeline.clips) == 1  # rien n'a bougé
