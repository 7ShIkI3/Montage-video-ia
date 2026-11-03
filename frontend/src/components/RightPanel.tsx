import { useMemo, useRef, useState } from "react";
import { api, fmtTime, waitJob } from "../api";
import { clipStarts, useStore } from "../store";

export function CommandBar() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const dirty = useStore((s) => s.dirty);
  const { setJob, notify } = useStore.getState();
  const [text, setText] = useState("");
  const busy = !!job && (job.status === "en_cours" || job.status === "en_attente");
  if (!project) return null;
  const examples = ["Fais une version de 30 secondes", "Passe en vertical", "Enlève le passage sur « la météo »", "Ajoute de la musique"];

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

export function Inspector() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const selected = useStore((s) => s.selected);
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
        </>
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
              const t = start + (w.start - c.src_in);
              const isNow = playhead >= t && playhead < start + (w.end - c.src_in);
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
