"""Recherche web : réponse Claude avec sources, repli DuckDuckGo, notes données au réalisateur."""
import types

import pytest

from app import config
from app.ai import research

DDG_PAGE = """
<div class="result"><h2 class="result__title">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.sportskeeda.com%2Fminecraft%2Funwritten%2Drules&amp;rut=abc">7 unwritten rules of <b>Minecraft</b> PvP - Sportskeeda</a>
</h2><a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">PvP, or player vs. player, is a <b>fundamental</b> game mode &amp; more.</a></div>
<div class="result"><h2 class="result__title">
<a rel="nofollow" class="result__a" href="https://mctiers.gg/">MCTiers</a></h2>
<a class="result__snippet" href="https://mctiers.gg/">Tier list.</a></div>
<div class="result"><a class="result__a" href="javascript:void(0)">Pub</a></div>
"""


def test_ddg_page_is_parsed_into_clean_results():
    rs = research.parse_ddg(DDG_PAGE)
    assert [r["url"] for r in rs] == ["https://www.sportskeeda.com/minecraft/unwritten-rules", "https://mctiers.gg/"]
    assert rs[0]["title"] == "7 unwritten rules of Minecraft PvP - Sportskeeda"
    assert rs[0]["snippet"] == "PvP, or player vs. player, is a fundamental game mode & more."


def test_local_fallback_lists_results_and_sources(monkeypatch):
    monkeypatch.setattr(research, "ddg_search", lambda q, limit=6: research.parse_ddg(DDG_PAGE))
    monkeypatch.setattr(config, "claude_available", lambda: False)
    out = research.research("règles pvp minecraft")
    assert out["by"] == "local" and len(out["sources"]) == 2
    assert "Sportskeeda" in out["answer"] and "clé Claude" in out["answer"]
    monkeypatch.setattr(research, "ddg_search", lambda q, limit=6: [])
    assert research.research("x")["sources"] == []
    with pytest.raises(ValueError):
        research.research("   ")


def _block(**kw):
    return types.SimpleNamespace(**kw)


def test_claude_response_gives_text_and_deduplicated_sources():
    cit = _block(type="web_search_result_location", title="Wiki", url="https://wiki.example/a")
    r = _block(stop_reason="end_turn", content=[
        _block(type="server_tool_use", name="web_search"),
        _block(type="web_search_tool_result", content=[
            _block(type="web_search_result", title="Wiki", url="https://wiki.example/a"),
            _block(type="web_search_result", title="Autre", url="https://autre.example/b"),
        ]),
        _block(type="text", text="Le PvP est apparu en 2010. ", citations=[cit]),
        _block(type="text", text="Idées : chiffre choc.", citations=None),
    ])
    out = research.parse_claude_response(r)
    assert out["by"] == "claude" and out["answer"] == "Le PvP est apparu en 2010. Idées : chiffre choc."
    assert out["sources"] == [{"title": "Wiki", "url": "https://wiki.example/a"}, {"title": "Autre", "url": "https://autre.example/b"}]
    with pytest.raises(RuntimeError):
        research.parse_claude_response(_block(stop_reason="refusal", content=[_block(type="text", text="non", citations=None)]))
    with pytest.raises(RuntimeError):
        research.parse_claude_response(_block(stop_reason="end_turn", content=[]))


def test_claude_failure_falls_back_to_duckduckgo(monkeypatch):
    monkeypatch.setattr(config, "claude_available", lambda: True)
    monkeypatch.setattr(research, "_claude", lambda q, c: (_ for _ in ()).throw(RuntimeError("quota")))
    monkeypatch.setattr(research, "ddg_search", lambda q, limit=6: research.parse_ddg(DDG_PAGE))
    steps = []
    ctx = types.SimpleNamespace(step=lambda msg, p=None: steps.append(msg))
    out = research.research("pvp", ctx=ctx)
    assert out["by"] == "local" and steps == ["Claude cherche sur le web", "Recherche DuckDuckGo"]


def test_research_route_stores_a_note_and_the_director_gets_it(monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from app import pipeline
    from app.main import app
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "Doc"}).json()["id"]
    monkeypatch.setattr(research, "research", lambda q, c="", ctx=None: {"answer": f"Réponse à {q} (contexte : {c})",
                                                                          "sources": [{"title": "S", "url": "https://s.example"}], "by": "local"})
    assert client.post(f"/api/projects/{pid}/research", json={"question": "  "}).status_code == 400
    job = client.post(f"/api/projects/{pid}/research", json={"question": "Quand est né le PvP ?"}).json()
    for _ in range(100):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            break
        time.sleep(0.05)
    assert j["status"] == "termine", j
    p = client.get(f"/api/projects/{pid}").json()
    assert len(p["research"]) == 1 and p["research"][0]["question"] == "Quand est né le PvP ?"
    assert "Projet « Doc »" in p["research"][0]["answer"] and p["research"][0]["sources"][0]["url"] == "https://s.example"
    nid = p["research"][0]["id"]
    assert client.delete(f"/api/projects/{pid}/research/{nid}").json()["ok"]
    assert client.delete(f"/api/projects/{pid}/research/{nid}").status_code == 404
    assert client.get(f"/api/projects/{pid}").json()["research"] == []
    client.delete(f"/api/projects/{pid}")
    assert "research_mod.notes_as_instructions" in open(pipeline.__file__, encoding="utf-8").read()


def test_notes_become_a_bounded_instruction():
    n = lambda q, a: types.SimpleNamespace(question=q, answer=a)  # noqa: E731
    assert research.notes_as_instructions([]) == ""
    text = research.notes_as_instructions([n("Q1", "R1"), n("Q2", "R2 " * 2000), n("Q3", "R3")], max_chars=300)
    assert text.startswith("Notes de recherche") and "Q3 → R3" in text and "Q1" not in text  # la plus récente d'abord, budget borné
    assert len(text) < 420 and text.rstrip().endswith("…")
