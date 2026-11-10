import { useMemo, useRef, useState } from "react";
import { api, Clip, EffectsCatalog, fmtTime, Timeline } from "../api";
import { clipAt, clipStarts, commentTime, ItemKind, srcToLocal, useStore } from "../store";
import { Icon } from "../icons";
import { MusicLibraryDialog } from "./Library";
import { ask } from "./Dialog";
import { ClipInspector } from "./right/ClipInspector";
import { ItemHead, RangeField } from "./right/fields";
import { EmptyHint } from "./ui/EmptyHint";

// Commentaires de montage : posés à un instant (ou sur tout le montage), appliqués en une passe.
// La zone de saisie est en haut, la liste défile seule, le bouton « Appliquer » reste en bas.
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
        useStore.getState().notify(`Commentaires : ${(done.result as { message?: string })?.message ?? "appliqués"}.`);
      },
    });
  };
  const label = { ouvert: "En attente", applique: "Appliqué", incompris: "Pas compris" } as const;
  const pickWhy = busy ? "Une tâche est en cours." : !hit ? "Place le curseur sur un plan pour sélectionner un élément de l'image." : null;
  const applyWhy = busy ? "Une tâche est en cours." : !open.length ? "Aucun commentaire en attente à appliquer." : null;

  return (
    <section className="panel comments">
      <div className="cm-compose">
        <textarea id="comment-box" rows={2} value={text} disabled={busy} aria-label="Commentaire de montage"
          placeholder={hit && !general ? `À ${fmtTime(playhead, true)} sur ${clipName} : « coupe ce passage », « ralenti », « texte "Wow" »…` : "Remarque sur tout le montage…"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); add(); } }} />
        {pending ? (
          <div className="zone-pill">
            <span><Icon name="square" size={12} /> Zone sélectionnée sur l'image</span>
            <button className="icon-btn" onClick={() => setPendingRegion(null)} title="Retirer la zone" aria-label="Retirer la zone"><Icon name="x" size={14} /></button>
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
            onClick={() => setPicking(!picking)}>
            <Icon name="square" size={12} />{picking ? "Trace la zone…" : "Sélectionner sur la vidéo"}
          </button>
          <button className="small-btn primary-soft" disabled={!text.trim() || busy} onClick={add}><Icon name="message" size={13} />Ajouter</button>
        </div>
        {pickWhy && <p className="why-off">{pickWhy}</p>}
        <label className="inline small cm-general"><input type="checkbox" checked={general || !hit} disabled={!hit || !!pending}
          onChange={(e) => setGeneral(e.target.checked)} /> Tout le montage</label>
      </div>
      {sorted.length > 0 ? (
        <ul className="comment-list">
          {sorted.map((c) => {
            const t = commentTime(tl, c);
            return (
              <li key={c.id} className={`cm cm-${c.status} ${item?.kind === "comment" && item.id === c.id ? "sel" : ""}`}
                onClick={() => { selectItem({ kind: "comment", id: c.id }); if (t != null) setPlayhead(t); }}>
                <div className="cm-head">
                  <b>{c.media_id ? (t != null ? fmtTime(t, true) : "passage retiré") : "Général"}</b>
                  <span className={`cm-status s-${c.status}`}>{label[c.status]}</span>
                  <button className="icon-btn" title="Supprimer" onClick={(e) => { e.stopPropagation(); deleteItem("comment", c.id); }} aria-label="Supprimer le commentaire"><Icon name="x" size={13} /></button>
                </div>
                <div>{c.region && <span className="zone-tag" title="Commentaire sur une zone de l'image"><Icon name="square" size={11} strokeWidth={2.4} /></span>} {c.text}</div>
                {c.reply && <div className="cm-reply">Réponse : {c.reply}</div>}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted small cm-empty">Les commentaires restent attachés au moment filmé : ils sont respectés même quand tout le montage est refait.</p>
      )}
      <div className="cm-foot">
        <button className="primary" disabled={!open.length || busy} onClick={apply}>
          {busy && job?.kind === "commentaires" ? "Application…" : `Appliquer les commentaires (${open.length})`}
        </button>
        {applyWhy && <p className="why-off">{applyWhy}</p>}
      </div>
    </section>
  );
}

/** Réglages du plan sélectionné : en-tête, raison, note, puis sous-onglets (voir `right/ClipInspector`). */
export function ClipEditor({ c }: { c: Clip }) {
  return <ClipInspector c={c} />;
}

/** Passages écartés par le montage, réintégrables. La liste est dans une section repliable du panneau Film. */
export function RejectedList() {
  const tl = useStore((s) => s.timeline);
  const segments = useStore((s) => s.segments);
  if (!tl || !tl.rejected.length) return null;
  const segText = (id: string | null) => segments.find((s) => s.id === id)?.text ?? "";
  return (
    <ul className="rejected-list">
      {tl.rejected.slice(0, 150).map((r) => (
        <li key={r.id}>
          <div className="small"><b className="mono">{fmtTime(r.src_in, true)}</b> {segText(r.segment_id).slice(0, 90) || `${(r.src_out - r.src_in).toFixed(1)} s`}</div>
          <div className="muted small">{r.reason}</div>
          <button className="small-btn" onClick={() => useStore.getState().restoreRejected(r)}>Réintégrer</button>
        </li>
      ))}
    </ul>
  );
}

/** Musique du montage : bibliothèque, fichier, volume, baisse sous la voix. Le titre est porté par la section qui l'accueille. */
export function MusicSection() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const { setTimelineProps, notify } = useStore.getState();
  const musicInput = useRef<HTMLInputElement>(null);
  const [libOpen, setLibOpen] = useState(false);
  if (!tl || !project) return null;
  return (
    <div className="music-section">
      {libOpen && <MusicLibraryDialog current={tl.music.track} onClose={() => setLibOpen(false)}
        onPick={(path) => setTimelineProps({ music: { ...tl.music, track: path } })} />}
      <p className="music-name">{tl.music.track ? tl.music.track.split(/[\\/]/).pop() : "Aucune musique"}</p>
      <div className="row-btns">
        <button className="small-btn" onClick={() => setLibOpen(true)}><Icon name="music" size={13} />Bibliothèque</button>
        <button className="small-btn" onClick={() => musicInput.current?.click()}><Icon name="upload" size={13} />Fichier</button>
        {tl.music.track && <button className="small-btn danger" onClick={() => setTimelineProps({ music: { ...tl.music, track: null } })}><Icon name="trash" size={13} />Retirer</button>}
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
      {!tl.music.track && <p className="muted small">Bibliothèque : musiques libres de droits, ou le son d'un lien.</p>}
      {tl.music.track && (
        <>
          <RangeField label="Volume de la musique" min={0} max={1} step={0.01} value={tl.music.volume} display={`${Math.round(tl.music.volume * 100)} %`}
            onChange={(v) => setTimelineProps({ music: { ...tl.music, volume: v } })} />
          <label className="inline"><input type="checkbox" className="switch" checked={tl.music.ducking}
            onChange={(e) => setTimelineProps({ music: { ...tl.music, ducking: e.target.checked } })} /> Baisser la musique sous la voix</label>
        </>
      )}
    </div>
  );
}

/** Look global : étalonnage et décompte des ajouts. Les ajouts (texte, bruitage, impact, image) se font depuis le panneau de gauche. */
export function GlobalFx({ tl, catalog }: { tl: Timeline; catalog: EffectsCatalog }) {
  const { setTimelineProps } = useStore.getState();
  const n = tl.clips.reduce((a, c) => a + c.effects.length + c.audio_effects.length + (c.transition !== "aucune" ? 1 : 0), 0);
  const stats: [string, number][] = [
    ["effets et transitions", n], ["textes", tl.texts?.length ?? 0], ["bruitages", tl.sfx?.length ?? 0],
    ["plans de coupe", tl.overlays?.length ?? 0], ["impacts", tl.impacts?.length ?? 0],
  ];
  return (
    <div className="global-fx">
      <label>Étalonnage (look global)
        <select value={tl.look || "aucun"} onChange={(e) => setTimelineProps({ look: e.target.value })}>
          {catalog.looks.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
      </label>
      <ul className="fx-count">
        {stats.map(([name, v]) => <li key={name}><b className="mono">{v}</b> {name}</li>)}
      </ul>
    </div>
  );
}

export function ItemEditor() {
  const tl = useStore((s) => s.timeline);
  const it = useStore((s) => s.item);
  const project = useStore((s) => s.project);
  const catalog = useStore((s) => s.catalog);
  const { updateItem, deleteItem, selectItem } = useStore.getState();
  if (!tl || !it || it.kind === "clip" || !project) return null;
  const up = (patch: Record<string, unknown>) => updateItem(it.kind as ItemKind, it.id, patch);
  const del = () => deleteItem(it.kind as ItemKind, it.id);
  const back = () => selectItem(null);
  const delBtn = (text: string) => <button className="small-btn danger" onClick={del}><Icon name="trash" size={13} />{text}</button>;

  if (it.kind === "text") {
    const x = tl.texts.find((t) => t.id === it.id);
    if (!x) return null;
    return (
      <div className="rw item-editor">
        <ItemHead icon="type" title="Texte à l'écran" onBack={back} />
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
        {delBtn("Supprimer le texte")}
      </div>
    );
  }
  if (it.kind === "impact") {
    const x = (tl.impacts ?? []).find((t) => t.id === it.id);
    if (!x) return null;
    const located = x.kind === "etincelles" || x.kind === "onde";
    return (
      <div className="rw item-editor">
        <ItemHead icon="zap" title="Impact 3D" onBack={back} />
        {x.reason && <p className="muted small">{x.reason}</p>}
        <div className="field-row">
          <label>Effet
            <select value={x.kind} onChange={(e) => up({ kind: e.target.value })}>
              {(catalog?.impacts ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </label>
          <label>Position (s)<input type="number" step={0.02} value={x.start.toFixed(2)} onChange={(e) => up({ start: Math.max(0, +e.target.value) })} /></label>
        </div>
        <RangeField label="Intensité" min={0.2} max={2.5} step={0.05} value={x.intensity} display={`${Math.round(x.intensity * 100)} %`} onChange={(v) => up({ intensity: v })} />
        {located && (
          <>
            <RangeField label="Point X" min={0} max={1} step={0.01} value={x.x} display={`${Math.round(x.x * 100)} %`} onChange={(v) => up({ x: v })} />
            <RangeField label="Point Y" min={0} max={1} step={0.01} value={x.y} display={`${Math.round(x.y * 100)} %`} onChange={(v) => up({ y: v })} />
          </>
        )}
        {delBtn("Supprimer l'impact")}
      </div>
    );
  }
  if (it.kind === "audio") {
    const a = (tl.audio || []).find((x) => x.id === it.id);
    if (!a) return null;
    const full = a.src_out;
    return (
      <div className="rw item-editor">
        <ItemHead icon={a.kind === "voix" ? "mic" : a.kind === "musique" ? "music" : "volume"}
          title={a.kind === "voix" ? "Voix off" : a.kind === "musique" ? "Musique" : "Son"} onBack={back} />
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
        <RangeField label="Couper le début" min={0} max={Math.max(0, full - 0.1)} step={0.05} value={a.src_in} display={`${a.src_in.toFixed(2)} s`}
          onChange={(v) => up({ src_in: Math.min(v, a.src_out - 0.1) })} />
        <RangeField label="Volume" min={0} max={2} step={0.05} value={a.volume} display={`${Math.round(a.volume * 100)} %`} onChange={(v) => up({ volume: v })} />
        <RangeField label="Fondu d'entrée" min={0} max={3} step={0.1} value={a.fade_in} display={`${a.fade_in.toFixed(1)} s`} onChange={(v) => up({ fade_in: v })} />
        <RangeField label="Fondu de sortie" min={0} max={3} step={0.1} value={a.fade_out} display={`${a.fade_out.toFixed(1)} s`} onChange={(v) => up({ fade_out: v })} />
        <p className="muted small">La musique de fond baisse toute seule sous la voix off quand « baisse auto » est activée.</p>
        <button className="danger" onClick={del}><Icon name="trash" size={13} />Supprimer ce son</button>
      </div>
    );
  }

  if (it.kind === "image") {
    const x = (tl.images ?? []).find((t) => t.id === it.id);
    if (!x) return null;
    // Neuf emplacements : chaque bouton porte un nom, la flèche décorative n'existe plus.
    const spots: [string, number, number][] = [["Haut gauche", 0.1, 0.1], ["Haut", 0.5, 0.1], ["Haut droite", 0.9, 0.1],
      ["Gauche", 0.1, 0.5], ["Centre", 0.5, 0.5], ["Droite", 0.9, 0.5],
      ["Bas gauche", 0.1, 0.9], ["Bas", 0.5, 0.9], ["Bas droite", 0.9, 0.9]];
    return (
      <div className="rw item-editor">
        <ItemHead icon="image" title="Image" onBack={back} />
        <img className="asset-thumb" src={api.assetUrl(project.id, x.asset)} alt="" />
        <label className="inline">
          <input type="checkbox" className="switch" checked={x.duration == null} onChange={(e) => up(e.target.checked ? { duration: null, start: 0 } : { duration: 3 })} />
          Sur toute la vidéo (filigrane)
        </label>
        {x.duration != null && (
          <div className="field-row">
            <label>Début (s)<input type="number" step={0.1} value={x.start.toFixed(2)} onChange={(e) => up({ start: Math.max(0, +e.target.value) })} /></label>
            <label>Durée (s)<input type="number" step={0.1} value={x.duration.toFixed(2)} onChange={(e) => up({ duration: Math.max(0.3, +e.target.value) })} /></label>
          </div>
        )}
        <div className="fx-title">Position</div>
        <div className="spot-grid" role="group" aria-label="Position de l'image">
          {spots.map(([l, sx, sy]) => {
            const on = Math.abs(x.x - sx) < 0.02 && Math.abs(x.y - sy) < 0.02;
            return (
              <button key={l} type="button" className={`spot-btn ${on ? "on" : ""}`} aria-label={l} aria-pressed={on} onClick={() => up({ x: sx, y: sy })}>
                <Icon name="dot" size={14} />
              </button>
            );
          })}
        </div>
        <RangeField label="Taille (largeur)" min={0.04} max={1} step={0.01} value={x.scale} display={`${Math.round(x.scale * 100)} %`} onChange={(v) => up({ scale: v })} />
        <RangeField label="Opacité" min={0.1} max={1} step={0.05} value={x.opacity} display={`${Math.round(x.opacity * 100)} %`} onChange={(v) => up({ opacity: v })} />
        <label>Animation
          <select value={x.anim} onChange={(e) => up({ anim: e.target.value })}>
            <option value="aucune">Aucune</option><option value="fondu">Fondu</option><option value="pop">Pop</option>
          </select>
        </label>
        {delBtn("Retirer l'image")}
      </div>
    );
  }
  if (it.kind === "marker") {
    const x = (tl.markers ?? []).find((t) => t.id === it.id);
    if (!x) return null;
    return (
      <div className="rw item-editor">
        <ItemHead icon="flag" title="Repère" onBack={back} />
        <label>Libellé
          <input value={x.label} placeholder="Ex. : Intro, Le marché, Conclusion" onChange={(e) => up({ label: e.target.value })} />
        </label>
        <div className="field-row">
          <label>Position (s)<input type="number" step={0.05} value={x.t.toFixed(2)} onChange={(e) => up({ t: Math.max(0, +e.target.value) })} /></label>
          <label>Couleur
            <select value={x.color} onChange={(e) => up({ color: e.target.value })}>
              {(["corail", "jaune", "vert", "bleu", "violet"] as const).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        </div>
        <label className="inline"><input type="checkbox" className="switch" checked={x.chapter} onChange={(e) => up({ chapter: e.target.checked })} /> Chapitre YouTube (kit de publication)</label>
        <p className="muted small">Les repères « chapitre » deviennent les chapitres de la description du kit (premier à 0:00, 10 s minimum, au moins 3). Tous passent en repères dans l'export XML.</p>
        <div className="row-btns">
          <button className="small-btn" onClick={() => useStore.getState().setPlayhead(x.t)}><Icon name="crosshair" size={13} />Aller au repère</button>
          {delBtn("Supprimer")}
        </div>
      </div>
    );
  }
  if (it.kind === "sfx") {
    const x = tl.sfx.find((t) => t.id === it.id);
    if (!x) return null;
    return (
      <div className="rw item-editor">
        <ItemHead icon="volume" title="Bruitage" onBack={back} />
        <div className="field-row">
          <label>Son
            <select value={x.kind} onChange={(e) => up({ kind: e.target.value })}>
              {(catalog?.sfx ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </label>
          <label>Position (s)<input type="number" step={0.05} value={x.start.toFixed(2)} onChange={(e) => up({ start: Math.max(0, +e.target.value) })} /></label>
        </div>
        <RangeField label="Volume" min={0} max={1.5} step={0.05} value={x.volume} display={`${Math.round(x.volume * 100)} %`} onChange={(v) => up({ volume: v })} />
        <div className="row-btns">
          <button className="small-btn" onClick={() => new Audio(api.sfxUrl(x.kind)).play().catch(() => undefined)}><Icon name="play" size={12} />Écouter</button>
          {delBtn("Supprimer")}
        </div>
      </div>
    );
  }
  const x = tl.overlays.find((t) => t.id === it.id);
  if (!x) return null;
  const m = project.media.find((mm) => mm.id === x.media_id);
  return (
    <div className="rw item-editor">
      <ItemHead icon="layers" title="Plan de coupe (B-roll)" onBack={back} />
      <p className="muted small">{m?.filename} · {x.reason}</p>
      <label>Affichage
        <select value={x.mode} onChange={(e) => up({ mode: e.target.value })}>
          <option value="plein">Plein écran</option><option value="incrustation">Incrustation (coin)</option>
          <option value="partage">Écran partagé (moitié-moitié)</option>
        </select>
      </label>
      {x.mode === "partage" && (
        <RangeField label="Cadrage du plan de coupe (gauche ↔ droite)" min={0} max={1} step={0.01} value={x.focus_x ?? 0.5}
          display={`${Math.round((x.focus_x ?? 0.5) * 100)} %`} onChange={(v) => up({ focus_x: v })} />
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
      {delBtn("Retirer le plan de coupe")}
    </div>
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
    return (
      <section className="panel transcript">
        <EmptyHint icon="align-left" text="La transcription apparaît après le montage automatique."
          action={tl.clips.length ? undefined : { label: "Monter avec l'IA", onClick: () => useStore.getState().openLeft("ia") }} />
      </section>
    );

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
      <div className="tr-bar">
        <button className="small-btn" disabled={!sel} onClick={cut}><Icon name="scissors" size={13} />Couper la sélection</button>
        <p className="muted small">{sel ? "Maj+clic étend la sélection." : "Clic : aller au mot · Maj+clic : sélectionner · double-clic : corriger"}</p>
      </div>
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
                    const nt = await ask.prompt("Corriger le mot :", w.text, { title: "Corriger le sous-titre", ok: "Corriger" });
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
