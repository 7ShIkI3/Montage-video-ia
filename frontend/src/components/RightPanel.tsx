import { useMemo, useRef, useState } from "react";
import { api, Clip, EffectsCatalog, fmtTime, Opt, Timeline } from "../api";
import { CapHint } from "../caps";
import { clipAt, clipStarts, commentTime, ItemKind, srcToLocal, useStore } from "../store";
import { ImageButtons } from "./Studio";
import { CaptionLanguage } from "./AudioTools";
import { MotionEditor } from "./Motion";
import { CUTOUTS, CutoutTools, DubPanel, AIVoiceoverPanel, LutPicker } from "./Lot3";
import { PlanPanel, QualityPanel } from "./Quality";

export function CommandBar() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const caps = useStore((s) => s.caps);
  const [text, setText] = useState("");
  const busy = !!job && (job.status === "en_cours" || job.status === "en_attente");
  if (!project) return null;
  const examples = ["Fais une version de 30 secondes", "Passe en vertical", "Look cinéma", "Ajoute des transitions et des bruitages",
    "Ralenti sur l'action", "Enlève le passage sur « la météo »"];

  const send = async (instruction: string) => {
    if (!instruction.trim()) return;
    const before = useStore.getState().timeline;
    await useStore.getState().runJob("Consigne", () => api.command(project.id, instruction), {
      timeline: true,
      onDone: (done) => {
        // La consigne IA devient une étape annulable comme une retouche manuelle.
        if (before) useStore.setState((s) => ({ past: [...s.past, before], future: [] }));
        const r = done.result as { message?: string } | null;
        useStore.getState().notify(r?.message ? `IA : ${r.message}` : "Consigne appliquée.");
        setText("");
      },
    });
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
      <CapHint reason={caps && !caps.claude ? "Sans Claude, seules les consignes simples (durée, format, sous-titres, musique, zooms…) sont comprises." : null} />
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
  const { addComment, deleteItem, setPlayhead, selectItem, setPicking, setPendingRegion } = useStore.getState();
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
    await useStore.getState().guard("Enregistrement impossible", () => useStore.getState().saveNow());
    const before = useStore.getState().timeline;
    await useStore.getState().runJob("Commentaires", () => api.applyComments(project.id), {
      timeline: true,
      onDone: (done) => {
        if (before) useStore.setState((s) => ({ past: [...s.past, before], future: [] }));
        useStore.getState().notify(`IA : ${(done.result as { message?: string })?.message ?? "commentaires appliqués"}.`);
      },
    });
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
          <MotionEditor c={c} />
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
          <PlanPanel />
          <QualityPanel />
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
          <CaptionLanguage />
          <LutPicker />
          <DubPanel />
          <AIVoiceoverPanel />
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
      </div>
      {c.transition !== "aucune" && (
        <label>Durée de transition {c.transition_duration.toFixed(2)} s
          <input type="range" min={0.1} max={1.5} step={0.05} value={c.transition_duration}
            onChange={(e) => updateClip(c.id, { transition_duration: +e.target.value })} />
        </label>
      )}
      {groups.map((g) => (
        <div key={g}>
          <div className="fx-title">{GROUP_LABELS[g] ?? g}</div>
          <Chips opts={catalog.video_effects.filter((o) => (o.group || "Effets") === g)} value={c.effects}
            onToggle={(id) => updateClip(c.id, {
              // Un seul fond de détourage à la fois sur un plan.
              effects: CUTOUTS.includes(id) && !c.effects.includes(id)
                ? [...c.effects.filter((e) => !CUTOUTS.includes(e)), id] : toggle(c.effects, id),
            })} />
          {g === "ia" && <CutoutTools c={c} />}
        </div>
      ))}
      <div className="fx-title">Effets sur la voix</div>
      <Chips opts={catalog.audio_effects} value={c.audio_effects}
        onToggle={(id) => updateClip(c.id, { audio_effects: toggle(c.audio_effects, id) })} />
    </div>
  );
}

const GROUP_LABELS: Record<string, string> = {
  couleur: "Couleur", style: "Style", mouvement: "Mouvement", "3d": "3D", ia: "Détourage IA (sans fond vert)",
};

function GlobalFx({ tl, catalog }: { tl: Timeline; catalog: EffectsCatalog }) {
  const { setTimelineProps, addText, addSfx, addImpact } = useStore.getState();
  const [kind, setKind] = useState(catalog.sfx[0]?.id ?? "whoosh");
  const [ik, setIk] = useState(catalog.impacts?.[0]?.id ?? "secousse");
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
        {tl.overlays?.length ?? 0} plan(s) de coupe · {tl.impacts?.length ?? 0} impact(s)
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
      <div className="sfx-row">
        <select value={ik} onChange={(e) => setIk(e.target.value)}>
          {(catalog.impacts ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
        <button className="small-btn" onClick={() => addImpact(ik)}>＋ Impact 3D</button>
      </div>
      <ImageButtons />
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
  const up = (patch: Record<string, unknown>) => updateItem(it.kind as ItemKind, it.id, patch);
  const del = () => deleteItem(it.kind as ItemKind, it.id);
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
  if (it.kind === "impact") {
    const x = (tl.impacts ?? []).find((t) => t.id === it.id);
    if (!x) return null;
    const located = x.kind === "etincelles" || x.kind === "onde";
    return (
      <>
        <header><h3>Impact 3D</h3>{back}</header>
        {x.reason && <p className="muted small">{x.reason}</p>}
        <div className="field-row">
          <label>Effet
            <select value={x.kind} onChange={(e) => up({ kind: e.target.value })}>
              {(catalog?.impacts ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </label>
          <label>Position (s)<input type="number" step={0.02} value={x.start.toFixed(2)} onChange={(e) => up({ start: Math.max(0, +e.target.value) })} /></label>
        </div>
        <label>Intensité {Math.round(x.intensity * 100)} %
          <input type="range" min={0.2} max={2.5} step={0.05} value={x.intensity} onChange={(e) => up({ intensity: +e.target.value })} />
        </label>
        {located && (
          <div className="field-row">
            <label>Point X {Math.round(x.x * 100)} %<input type="range" min={0} max={1} step={0.01} value={x.x} onChange={(e) => up({ x: +e.target.value })} /></label>
            <label>Point Y {Math.round(x.y * 100)} %<input type="range" min={0} max={1} step={0.01} value={x.y} onChange={(e) => up({ y: +e.target.value })} /></label>
          </div>
        )}
        <button className="small-btn danger" onClick={del}>Supprimer l'impact</button>
      </>
    );
  }
  if (it.kind === "audio") {
    const a = (tl.audio || []).find((x) => x.id === it.id);
    if (!a) return null;
    const full = a.src_out;
    return (
      <>
        <header><h3>{a.kind === "voix" ? "Voix off" : a.kind === "musique" ? "Musique" : "Son"}</h3>{back}</header>
        <audio controls src={api.audioUrl(project.id, a.asset)} style={{ width: "100%" }} />
        {a.text && <p className="small">« {a.text} »</p>}
        {a.check && (
          <p className={`small voice-check ${a.check.ok === false ? "bad" : a.check.ok ? "good" : ""}`}>
            {a.check.method === "whisper"
              ? a.check.ok ? `Réécoutée : bien comprise (${Math.round((1 - (a.check.wer ?? 0)) * 100)} % des mots).`
                : `Réécoutée : mal comprise, entendu « ${a.check.heard} ». Reformule le texte ou change de voix.`
              : a.check.ok === false ? "Réécoute : aucun son produit." : "Mots horodatés sur le son (Whisper absent : compréhension non vérifiée)."}
            {a.words?.length ? ` ${a.words.length} mots calés.` : ""}
          </p>
        )}
        <label>Nom<input value={a.label} onChange={(e) => up({ label: e.target.value })} /></label>
        <div className="field-row">
          <label>Type
            <select value={a.kind} onChange={(e) => up({ kind: e.target.value })}>
              <option value="voix">Voix off</option><option value="son">Son</option><option value="musique">Musique</option>
            </select>
          </label>
          <label>Piste
            <select value={a.lane} onChange={(e) => up({ lane: +e.target.value })}>
              {[0, 1, 2, 3].map((l) => <option key={l} value={l}>Audio {l + 1}</option>)}
            </select>
          </label>
        </div>
        <div className="field-row">
          <label>Début (s)<input type="number" step={0.05} min={0} value={a.start.toFixed(2)} onChange={(e) => up({ start: Math.max(0, +e.target.value) })} /></label>
          <label>Durée (s)<input type="number" step={0.05} min={0.1} value={(a.src_out - a.src_in).toFixed(2)}
            onChange={(e) => up({ src_out: Math.max(a.src_in + 0.1, a.src_in + +e.target.value) })} /></label>
        </div>
        <label>Couper le début {a.src_in.toFixed(2)} s
          <input type="range" min={0} max={Math.max(0, full - 0.1)} step={0.05} value={a.src_in} onChange={(e) => up({ src_in: Math.min(+e.target.value, a.src_out - 0.1) })} />
        </label>
        <label>Volume {Math.round(a.volume * 100)} %
          <input type="range" min={0} max={2} step={0.05} value={a.volume} onChange={(e) => up({ volume: +e.target.value })} />
        </label>
        <div className="field-row">
          <label>Fondu d'entrée {a.fade_in.toFixed(1)} s
            <input type="range" min={0} max={3} step={0.1} value={a.fade_in} onChange={(e) => up({ fade_in: +e.target.value })} />
          </label>
          <label>Fondu de sortie {a.fade_out.toFixed(1)} s
            <input type="range" min={0} max={3} step={0.1} value={a.fade_out} onChange={(e) => up({ fade_out: +e.target.value })} />
          </label>
        </div>
        <p className="muted small">La musique de fond baisse toute seule sous la voix off quand « baisse auto » est activée.</p>
        <button className="danger" onClick={del}>Supprimer ce son</button>
      </>
    );
  }

  if (it.kind === "image") {
    const x = (tl.images ?? []).find((t) => t.id === it.id);
    if (!x) return null;
    const spots: [string, number, number][] = [["↖", 0.1, 0.1], ["↑", 0.5, 0.1], ["↗", 0.9, 0.1], ["←", 0.1, 0.5], ["•", 0.5, 0.5],
      ["→", 0.9, 0.5], ["↙", 0.1, 0.9], ["↓", 0.5, 0.9], ["↘", 0.9, 0.9]];
    return (
      <>
        <header><h3>Image</h3>{back}</header>
        <img className="asset-thumb" src={api.assetUrl(project.id, x.asset)} alt="" />
        <label className="inline">
          <input type="checkbox" checked={x.duration == null} onChange={(e) => up(e.target.checked ? { duration: null, start: 0 } : { duration: 3 })} />
          Sur toute la vidéo (filigrane)
        </label>
        {x.duration != null && (
          <div className="field-row">
            <label>Début (s)<input type="number" step={0.1} value={x.start.toFixed(2)} onChange={(e) => up({ start: Math.max(0, +e.target.value) })} /></label>
            <label>Durée (s)<input type="number" step={0.1} value={x.duration.toFixed(2)} onChange={(e) => up({ duration: Math.max(0.3, +e.target.value) })} /></label>
          </div>
        )}
        <div className="fx-title">Position</div>
        <div className="spot-grid">
          {spots.map(([l, sx, sy]) => (
            <button key={l} className={`fx-chip ${Math.abs(x.x - sx) < 0.02 && Math.abs(x.y - sy) < 0.02 ? "on" : ""}`} onClick={() => up({ x: sx, y: sy })}>{l}</button>
          ))}
        </div>
        <label>Taille {Math.round(x.scale * 100)} % de la largeur
          <input type="range" min={0.04} max={1} step={0.01} value={x.scale} onChange={(e) => up({ scale: +e.target.value })} />
        </label>
        <label>Opacité {Math.round(x.opacity * 100)} %
          <input type="range" min={0.1} max={1} step={0.05} value={x.opacity} onChange={(e) => up({ opacity: +e.target.value })} />
        </label>
        <label>Animation
          <select value={x.anim} onChange={(e) => up({ anim: e.target.value })}>
            <option value="aucune">Aucune</option><option value="fondu">Fondu</option><option value="pop">Pop</option>
          </select>
        </label>
        <button className="small-btn danger" onClick={del}>Retirer l'image</button>
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
          <option value="partage">Écran partagé (moitié-moitié)</option>
        </select>
      </label>
      {x.mode === "partage" && (
        <label>Cadrage du plan de coupe (gauche ↔ droite)
          <input type="range" min={0} max={1} step={0.01} value={x.focus_x ?? 0.5} onChange={(e) => up({ focus_x: +e.target.value })} />
        </label>
      )}
      <div className="field-row">
        <label>Source début<input type="number" step={0.05} value={x.src_in.toFixed(2)} onChange={(e) => up({ src_in: Math.max(0, Math.min(+e.target.value, x.src_out - 0.2)) })} /></label>
        <label>Source fin<input type="number" step={0.05} value={x.src_out.toFixed(2)} onChange={(e) => up({ src_out: Math.min(m?.duration ?? 1e9, Math.max(+e.target.value, x.src_in + 0.2)) })} /></label>
      </div>
      <label>Placé à (s)<input type="number" step={0.1} value={x.start.toFixed(2)} onChange={(e) => up({ start: Math.max(0, +e.target.value) })} /></label>
      <div className="field-row">
        <label>Fond à retirer
          <select value={x.chroma ?? "aucun"} onChange={(e) => up({ chroma: e.target.value })}>
            <option value="aucun">Aucun</option><option value="vert">Fond vert</option><option value="bleu">Fond bleu</option>
          </select>
        </label>
        {x.mode === "incrustation" && (
          <label>Coin
            <select value={x.corner ?? "haut_droite"} onChange={(e) => up({ corner: e.target.value })}>
              <option value="haut_droite">Haut droite</option><option value="haut_gauche">Haut gauche</option>
              <option value="bas_droite">Bas droite</option><option value="bas_gauche">Bas gauche</option>
            </select>
          </label>
        )}
      </div>
      {x.chroma && x.chroma !== "aucun" && <p className="muted small">Le fond est retiré à l'export ; l'aperçu le montre encore.</p>}
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
              const t = start + srcToLocal(c, w.start - c.src_in);
              const isNow = playhead >= t && playhead < start + srcToLocal(c, w.end - c.src_in);
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
                    await useStore.getState().guard("Correction impossible", async () => {
                      await api.editWord(project.id, c.media_id, idx, nt);
                      await useStore.getState().refreshDerived();
                    });
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
