import { useMemo, useRef, useState } from "react";
import { api, Clip, EffectsCatalog, fmtTime, Opt, Timeline, waitJob } from "../api";
import { clipAt, clipStarts, commentTime, useStore } from "../store";

export function CommandBar() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const dirty = useStore((s) => s.dirty);
  const { setJob, notify } = useStore.getState();
  const [text, setText] = useState("");
  const busy = !!job && (job.status === "en_cours" || job.status === "en_attente");
  if (!project) return null;
  const examples = ["Fais une version de 30 secondes", "Passe en vertical", "Look cinéma", "Ajoute des transitions et des bruitages",
    "Ralenti sur l'action", "Enlève le passage sur « la météo »"];

  const send = async (instruction: string) => {
    if (!instruction.trim()) return;
    try {
      if (dirty) {
        await api.saveTimeline(project.id, useStore.getState().timeline!);
        useStore.getState().markSaved();
      }
      const j = await api.command(project.id, instruction);
      const done = await waitJob(j, setJob);
      const before = useStore.getState().timeline;
      await useStore.getState().refreshProject();
      // La consigne IA devient une étape annulable comme une retouche manuelle.
      if (before) useStore.setState((s) => ({ past: [...s.past, before], future: [] }));
      const r = done.result as { message?: string } | null;
      notify(r?.message ? `IA : ${r.message}` : "Consigne appliquée.");
      setText("");
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setJob(null);
    }
  };

  return (
    <section className="panel command">
      <header><h3>Demande à l'IA</h3></header>
      <form onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <textarea rows={2} value={text} placeholder="Décris le changement voulu…" disabled={busy}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
        <button className="primary" disabled={busy || !text.trim() || !project.timeline.clips.length}>
          {busy && job?.kind === "consigne" ? "…" : "Appliquer"}
        </button>
      </form>
      <div className="chips">
        {examples.map((x) => <button key={x} className="chip" onClick={() => setText(x)}>{x}</button>)}
      </div>
    </section>
  );
}

// Commentaires de montage : posés à un instant (ou sur tout le montage), l'IA les lit et les applique.
export function CommentsPanel() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const playhead = useStore((s) => s.playhead);
  const job = useStore((s) => s.job);
  const item = useStore((s) => s.item);
  const dirty = useStore((s) => s.dirty);
  const { addComment, deleteItem, setPlayhead, selectItem, setJob, notify, setPicking, setPendingRegion } = useStore.getState();
  const picking = useStore((s) => s.picking);
  const pending = useStore((s) => s.pendingRegion);
  const [text, setText] = useState("");
  const [general, setGeneral] = useState(false);
  if (!tl || !project) return null;
  const comments = tl.comments || [];
  const open = comments.filter((c) => c.status === "ouvert");
  const busy = !!job && (job.status === "en_cours" || job.status === "en_attente");
  const hit = tl.clips.length ? clipAt(tl, playhead) : null;
  const clipName = hit ? project.media.find((m) => m.id === tl.clips[hit.index].media_id)?.filename : null;
  const sorted = [...comments].sort((a, b) => (commentTime(tl, a) ?? -1) - (commentTime(tl, b) ?? -1));

  const add = () => {
    if (addComment(text, general || !hit)) setText("");
  };
  const apply = async () => {
    try {
      if (dirty) {
        await api.saveTimeline(project.id, useStore.getState().timeline!);
        useStore.getState().markSaved();
      }
      const before = useStore.getState().timeline;
      const done = await waitJob(await api.applyComments(project.id), setJob);
      await useStore.getState().refreshProject();
      if (before) useStore.setState((s) => ({ past: [...s.past, before], future: [] }));
      notify(`IA : ${(done.result as { message?: string })?.message ?? "commentaires appliqués"}.`);
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setJob(null);
    }
  };
  const label = { ouvert: "En attente", applique: "Appliqué", incompris: "Pas compris" } as const;

  return (
    <section className="panel comments">
      <header>
        <h3>Commentaires pour l'IA</h3>
        <span className="muted small">{open.length} en attente</span>
      </header>
      <textarea id="comment-box" rows={2} value={text} disabled={busy}
        placeholder={hit && !general ? `À ${fmtTime(playhead, true)} sur ${clipName} : « coupe ce passage », « ralenti », « texte "Wow" »…` : "Remarque sur tout le montage…"}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); add(); } }} />
      {pending ? (
        <div className="zone-pill">
          <span>⬚ Zone sélectionnée sur l'image</span>
          <button className="link" onClick={() => setPendingRegion(null)} title="Retirer la zone">✕</button>
        </div>
      ) : null}
      {pending && (
        <div className="quick-actions">
          {[["Flouter", "Floute ça"], ["Pixeliser", "Pixelise ça"], ["Cacher", "Cache ça en noir"],
            ["Entourer", "Entoure ça"], ["Zoomer dessus", "Zoom sur ça"], ["Cadrer dessus", "Cadre sur ça"]].map(([l, t]) => (
            <button key={l} className="chip" disabled={busy} onClick={() => addComment(text.trim() ? `${t}. ${text.trim()}` : t) && setText("")}>{l}</button>
          ))}
        </div>
      )}
      <div className="comment-row">
        <button className={`small-btn pick-btn ${picking ? "on" : ""}`} disabled={!hit || busy}
          onClick={() => setPicking(!picking)} title="Sélectionner un élément directement sur l'image">
          ⬚ {picking ? "Trace la zone…" : "Sélectionner sur la vidéo"}
        </button>
        <label className="inline small"><input type="checkbox" checked={general || !hit} disabled={!hit || !!pending}
          onChange={(e) => setGeneral(e.target.checked)} /> Tout le montage</label>
        <button className="small-btn primary-soft" disabled={!text.trim() || busy} onClick={add}>💬 Ajouter</button>
      </div>
      {sorted.length > 0 && (
        <ul className="comment-list">
          {sorted.map((c) => {
            const t = commentTime(tl, c);
            return (
              <li key={c.id} className={`cm cm-${c.status} ${item?.kind === "comment" && item.id === c.id ? "sel" : ""}`}
                onClick={() => { selectItem({ kind: "comment", id: c.id }); if (t != null) setPlayhead(t); }}>
                <div className="cm-head">
                  <b>{c.media_id ? (t != null ? fmtTime(t, true) : "passage retiré") : "Général"}</b>
                  <span className={`cm-status s-${c.status}`}>{label[c.status]}</span>
                  <button className="link" title="Supprimer" onClick={(e) => { e.stopPropagation(); deleteItem("comment", c.id); }}>✕</button>
                </div>
                <div>{c.region && <span className="zone-tag" title="Commentaire sur une zone de l'image">⬚</span>} {c.text}</div>
                {c.reply && <div className="cm-reply">IA : {c.reply}</div>}
              </li>
            );
          })}
        </ul>
      )}
      <button className="primary" disabled={!open.length || busy} onClick={apply}>
        {busy && job?.kind === "commentaires" ? "L'IA applique…" : `Appliquer les commentaires (${open.length})`}
      </button>
      <p className="muted small">Les commentaires restent attachés au moment filmé : l'IA les respecte aussi quand elle refait tout le montage.</p>
    </section>
  );
}

export function Inspector() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const selected = useStore((s) => s.selected);
  const it = useStore((s) => s.item);
  const catalog = useStore((s) => s.catalog);
  const { updateClip, restoreRejected, setTimelineProps, notify } = useStore.getState();
  const segments = useStore((s) => s.segments);
  const [showRejected, setShowRejected] = useState(false);
  const musicInput = useRef<HTMLInputElement>(null);
  if (!tl || !project) return null;
  const c = tl.clips.find((x) => x.id === selected);
  const segText = (id: string | null) => segments.find((s) => s.id === id)?.text ?? "";

  return (
    <section className="panel inspector">
      {c ? (
        <>
          <header><h3>Plan sélectionné</h3></header>
          <p className="why"><b>Pourquoi l'IA l'a gardé :</b> {c.reason || "—"}</p>
          {segText(c.segment_id) && <p className="quote">« {segText(c.segment_id)} »</p>}
          <div className="score-row">Score <div className="bar"><div style={{ width: `${c.score * 100}%` }} /></div> {Math.round(c.score * 100)}</div>
          <div className="field-row">
            <label>Entrée (s)
              <input type="number" step={0.05} value={c.src_in.toFixed(2)}
                onChange={(e) => useStore.getState().trimClip(c.id, "in", +e.target.value)} />
            </label>
            <label>Sortie (s)
              <input type="number" step={0.05} value={c.src_out.toFixed(2)}
                onChange={(e) => useStore.getState().trimClip(c.id, "out", +e.target.value)} />
            </label>
          </div>
          <label>Zoom {Math.round((c.zoom - 1) * 100)} %
            <input type="range" min={1} max={1.5} step={0.01} value={c.zoom} onChange={(e) => updateClip(c.id, { zoom: +e.target.value })} />
          </label>
          <label>Volume {Math.round(c.volume * 100)} %
            <input type="range" min={0} max={2} step={0.05} value={c.volume} onChange={(e) => updateClip(c.id, { volume: +e.target.value })} />
          </label>
          {catalog && <ClipEffects c={c} catalog={catalog} />}
        </>
      ) : it && it.kind !== "clip" && it.kind !== "comment" ? (
        <ItemEditor />
      ) : (
        <>
          <header><h3>Montage</h3></header>
          {tl.title && <p className="title">{tl.title}</p>}
          <p className="muted small">{tl.summary || "Aucun montage pour l'instant."}</p>
          {tl.director && (
            <p className="muted small">Réalisé par : {tl.director === "claude" ? "Claude (IA)" : "moteur local"}</p>
          )}
          <p className="muted small">Clique un plan dans la timeline pour voir pourquoi l'IA l'a choisi.</p>
          <div className="field-row">
            <label>Format
              <select value={tl.format} onChange={(e) => setTimelineProps({ format: e.target.value as typeof tl.format })}>
                <option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option>
              </select>
            </label>
            <label>Sous-titres
              <select value={tl.captions} onChange={(e) => setTimelineProps({ captions: e.target.value as typeof tl.captions })}>
                <option value="karaoke">Animés</option><option value="classique">Classiques</option><option value="aucun">Aucun</option>
              </select>
            </label>
          </div>
          {tl.format !== "16:9" && (
            <label className="inline"><input type="checkbox" checked={tl.reframe !== false}
              onChange={(e) => setTimelineProps({ reframe: e.target.checked })} /> Recadrage auto sur le visage</label>
          )}
          {catalog && <GlobalFx tl={tl} catalog={catalog} />}
          <div className="music-row">
            <span>Musique : {tl.music.track ? tl.music.track.split(/[\\/]/).pop() : "aucune"}</span>
            <button className="small-btn" onClick={() => musicInput.current?.click()}>Choisir…</button>
            {tl.music.track && <button className="small-btn" onClick={() => setTimelineProps({ music: { ...tl.music, track: null } })}>Retirer</button>}
            <input ref={musicInput} type="file" accept="audio/*" hidden onChange={async (e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (!f) return;
              try {
                const m = await api.uploadMusic(project.id, f);
                setTimelineProps({ music: m });
              } catch (err) { notify((err as Error).message); }
            }} />
          </div>
          {tl.music.track && (
            <>
              <label>Volume musique {Math.round(tl.music.volume * 100)} %
                <input type="range" min={0} max={1} step={0.01} value={tl.music.volume}
                  onChange={(e) => setTimelineProps({ music: { ...tl.music, volume: +e.target.value } })} />
              </label>
              <label className="inline"><input type="checkbox" checked={tl.music.ducking}
                onChange={(e) => setTimelineProps({ music: { ...tl.music, ducking: e.target.checked } })} /> Baisser la musique sous la voix</label>
            </>
          )}
        </>
      )}
      {tl.rejected.length > 0 && (
        <div className="rejected">
          <button className="link" onClick={() => setShowRejected(!showRejected)}>
            {showRejected ? "▾" : "▸"} Passages écartés par l'IA ({tl.rejected.length})
          </button>
          {showRejected && (
            <ul>
              {tl.rejected.slice(0, 150).map((r) => (
                <li key={r.id}>
                  <div className="small"><b>{fmtTime(r.src_in, true)}</b> {segText(r.segment_id).slice(0, 90) || `${(r.src_out - r.src_in).toFixed(1)} s`}</div>
                  <div className="muted small">{r.reason}</div>
                  <button className="small-btn" onClick={() => restoreRejected(r)}>Réintégrer</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function Chips({ opts, value, onToggle }: { opts: Opt[]; value: string[]; onToggle: (id: string) => void }) {
  return (
    <div className="fx-chips">
      {opts.map((o) => (
        <button key={o.id} type="button" className={`fx-chip ${value.includes(o.id) ? "on" : ""}`} onClick={() => onToggle(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

function ClipEffects({ c, catalog }: { c: Clip; catalog: EffectsCatalog }) {
  const { updateClip } = useStore.getState();
  const groups = Array.from(new Set(catalog.video_effects.map((o) => o.group || "Effets")));
  return (
    <div className="fx-section">
      <div className="field-row">
        <label>Transition d'entrée
          <select value={c.transition} onChange={(e) => updateClip(c.id, { transition: e.target.value })}>
            {catalog.transitions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
        </label>
        <label>Vitesse
          <select value={c.speed} onChange={(e) => updateClip(c.id, { speed: +e.target.value })}>
            {catalog.speeds.map((v) => <option key={v} value={v}>{v === 1 ? "Normale" : `×${v}`}</option>)}
          </select>
        </label>
      </div>
      {c.transition !== "aucune" && (
        <label>Durée de transition {c.transition_duration.toFixed(2)} s
          <input type="range" min={0.1} max={1.5} step={0.05} value={c.transition_duration}
            onChange={(e) => updateClip(c.id, { transition_duration: +e.target.value })} />
        </label>
      )}
      {groups.map((g) => (
        <div key={g}>
          <div className="fx-title">{g}</div>
          <Chips opts={catalog.video_effects.filter((o) => (o.group || "Effets") === g)} value={c.effects}
            onToggle={(id) => updateClip(c.id, { effects: toggle(c.effects, id) })} />
        </div>
      ))}
      <div className="fx-title">Effets sur la voix</div>
      <Chips opts={catalog.audio_effects} value={c.audio_effects}
        onToggle={(id) => updateClip(c.id, { audio_effects: toggle(c.audio_effects, id) })} />
    </div>
  );
}

function GlobalFx({ tl, catalog }: { tl: Timeline; catalog: EffectsCatalog }) {
  const { setTimelineProps, addText, addSfx } = useStore.getState();
  const [kind, setKind] = useState(catalog.sfx[0]?.id ?? "whoosh");
  const n = tl.clips.reduce((a, c) => a + c.effects.length + c.audio_effects.length + (c.transition !== "aucune" ? 1 : 0), 0);
  return (
    <div className="fx-section">
      <label>Étalonnage (look global)
        <select value={tl.look || "aucun"} onChange={(e) => setTimelineProps({ look: e.target.value })}>
          {catalog.looks.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
      </label>
      <p className="muted small">
        {n} effet(s) et transition(s) sur les plans · {tl.texts?.length ?? 0} texte(s) · {tl.sfx?.length ?? 0} bruitage(s) ·{" "}
        {tl.overlays?.length ?? 0} plan(s) de coupe
      </p>
      <div className="field-row">
        <button className="small-btn" onClick={() => addText()}>＋ Texte au curseur</button>
      </div>
      <div className="sfx-row">
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {catalog.sfx.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
        <button className="small-btn" onClick={() => new Audio(api.sfxUrl(kind)).play().catch(() => undefined)}>▶</button>
        <button className="small-btn" onClick={() => addSfx(kind)}>＋ Bruitage</button>
      </div>
    </div>
  );
}

function ItemEditor() {
  const tl = useStore((s) => s.timeline);
  const it = useStore((s) => s.item);
  const project = useStore((s) => s.project);
  const catalog = useStore((s) => s.catalog);
  const { updateItem, deleteItem, selectItem } = useStore.getState();
  if (!tl || !it || it.kind === "clip" || !project) return null;
  const up = (patch: Record<string, unknown>) => updateItem(it.kind as "text" | "sfx" | "overlay", it.id, patch);
  const del = () => deleteItem(it.kind as "text" | "sfx" | "overlay", it.id);
  const back = <button className="link" onClick={() => selectItem(null)}>← Montage</button>;

  if (it.kind === "text") {
    const x = tl.texts.find((t) => t.id === it.id);
    if (!x) return null;
    return (
      <>
        <header><h3>Texte à l'écran</h3>{back}</header>
        <label>Texte<textarea rows={2} value={x.text} onChange={(e) => up({ text: e.target.value })} /></label>
        <div className="field-row">
          <label>Style
            <select value={x.style} onChange={(e) => up({ style: e.target.value })}>
              {(catalog?.text_styles ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </label>
          <label>Position
            <select value={x.position} onChange={(e) => up({ position: e.target.value })}>
              <option value="haut">Haut</option><option value="centre">Centre</option><option value="bas">Bas</option>
            </select>
          </label>
        </div>
        <div className="field-row">
          <label>Début (s)<input type="number" step={0.1} value={x.start.toFixed(2)} onChange={(e) => up({ start: Math.max(0, +e.target.value) })} /></label>
          <label>Durée (s)<input type="number" step={0.1} value={x.duration.toFixed(2)} onChange={(e) => up({ duration: Math.max(0.3, +e.target.value) })} /></label>
        </div>
        <button className="small-btn danger" onClick={del}>Supprimer le texte</button>
      </>
    );
  }
  if (it.kind === "sfx") {
    const x = tl.sfx.find((t) => t.id === it.id);
    if (!x) return null;
    return (
      <>
        <header><h3>Bruitage</h3>{back}</header>
        <div className="field-row">
          <label>Son
            <select value={x.kind} onChange={(e) => up({ kind: e.target.value })}>
              {(catalog?.sfx ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </label>
          <label>Position (s)<input type="number" step={0.05} value={x.start.toFixed(2)} onChange={(e) => up({ start: Math.max(0, +e.target.value) })} /></label>
        </div>
        <label>Volume {Math.round(x.volume * 100)} %
          <input type="range" min={0} max={1.5} step={0.05} value={x.volume} onChange={(e) => up({ volume: +e.target.value })} />
        </label>
        <div className="field-row">
          <button className="small-btn" onClick={() => new Audio(api.sfxUrl(x.kind)).play().catch(() => undefined)}>▶ Écouter</button>
          <button className="small-btn danger" onClick={del}>Supprimer</button>
        </div>
      </>
    );
  }
  const x = tl.overlays.find((t) => t.id === it.id);
  if (!x) return null;
  const m = project.media.find((mm) => mm.id === x.media_id);
  return (
    <>
      <header><h3>Plan de coupe (B-roll)</h3>{back}</header>
      <p className="muted small">{m?.filename} · {x.reason}</p>
      <label>Affichage
        <select value={x.mode} onChange={(e) => up({ mode: e.target.value })}>
          <option value="plein">Plein écran</option><option value="incrustation">Incrustation (coin)</option>
        </select>
      </label>
      <div className="field-row">
        <label>Source début<input type="number" step={0.05} value={x.src_in.toFixed(2)} onChange={(e) => up({ src_in: Math.max(0, Math.min(+e.target.value, x.src_out - 0.2)) })} /></label>
        <label>Source fin<input type="number" step={0.05} value={x.src_out.toFixed(2)} onChange={(e) => up({ src_out: Math.min(m?.duration ?? 1e9, Math.max(+e.target.value, x.src_in + 0.2)) })} /></label>
      </div>
      <label>Placé à (s)<input type="number" step={0.1} value={x.start.toFixed(2)} onChange={(e) => up({ start: Math.max(0, +e.target.value) })} /></label>
      <button className="small-btn danger" onClick={del}>Retirer le plan de coupe</button>
    </>
  );
}

// Transcription du montage : cliquer un mot y place le curseur, sélectionner des mots permet
// de les couper, double-cliquer corrige le sous-titre.
export function TranscriptPanel() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const transcripts = useStore((s) => s.transcripts);
  const playhead = useStore((s) => s.playhead);
  const { setPlayhead, cutSourceRange, notify } = useStore.getState();
  const [sel, setSel] = useState<{ clip: number; a: number; b: number } | null>(null);

  const items = useMemo(() => {
    if (!tl) return [];
    const starts = clipStarts(tl);
    return tl.clips.map((c, ci) => {
      const tr = transcripts[c.media_id];
      const words = (tr?.words ?? [])
        .map((w, idx) => ({ w, idx }))
        .filter(({ w }) => !w.filler && (w.start + w.end) / 2 >= c.src_in && (w.start + w.end) / 2 < c.src_out);
      return { c, ci, start: starts[ci], words };
    });
  }, [tl, transcripts]);

  if (!tl || !project) return null;
  if (!Object.keys(transcripts).length)
    return <section className="panel transcript"><header><h3>Texte</h3></header><p className="muted small">La transcription apparaîtra après le montage IA.</p></section>;

  const cut = () => {
    if (!sel) return;
    const it = items[sel.clip];
    const a = Math.min(sel.a, sel.b), b = Math.max(sel.a, sel.b);
    const ws = it.words.slice(a, b + 1).map((x) => x.w);
    if (!ws.length) return;
    cutSourceRange(it.c.media_id, ws[0].start - 0.03, ws[ws.length - 1].end + 0.03);
    setSel(null);
    notify(`${ws.length} mot(s) coupé(s) du montage.`);
  };

  return (
    <section className="panel transcript">
      <header>
        <h3>Texte</h3>
        <button className="small-btn" disabled={!sel} onClick={cut}>✂ Couper la sélection</button>
      </header>
      <p className="muted small">Clic : aller au mot · Maj+clic : sélectionner · double-clic : corriger</p>
      <div className="words">
        {items.map(({ c, ci, start, words }) => (
          <p key={c.id}>
            {words.map(({ w, idx }, wi) => {
              const sp = c.speed || 1;
              const t = start + (w.start - c.src_in) / sp;
              const isNow = playhead >= t && playhead < start + (w.end - c.src_in) / sp;
              const inSel = sel && sel.clip === ci && wi >= Math.min(sel.a, sel.b) && wi <= Math.max(sel.a, sel.b);
              return (
                <span key={idx} className={`w ${isNow ? "now" : ""} ${inSel ? "sel" : ""} ${w.prob < 0.5 ? "unsure" : ""}`}
                  onClick={(e) => {
                    if (e.shiftKey && sel && sel.clip === ci) setSel({ ...sel, b: wi });
                    else { setSel({ clip: ci, a: wi, b: wi }); setPlayhead(Math.max(0, t)); }
                  }}
                  onDoubleClick={async () => {
                    const nt = prompt("Corriger le mot :", w.text);
                    if (nt == null || nt === w.text) return;
                    await api.editWord(project.id, c.media_id, idx, nt);
                    await useStore.getState().refreshDerived();
                  }}>
                  {w.text}{" "}
                </span>
              );
            })}
          </p>
        ))}
      </div>
    </section>
  );
}
