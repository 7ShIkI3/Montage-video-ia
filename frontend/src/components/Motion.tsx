// Lot 2 : rampe de vitesse et images clés (zoom et cadrage animés) d'un plan.
import { Clip, fmtTime, Keyframe } from "../api";
import { clipDur, clipStarts, kfAt, useStore } from "../store";
import { Icon } from "../icons";

const RAMPS = [0.25, 0.4, 0.5, 0.75, 1, 1.5, 2, 3, 4];

export function MotionEditor({ c }: { c: Clip }) {
  const tl = useStore((s) => s.timeline);
  const playhead = useStore((s) => s.playhead);
  const { updateClip, setPlayhead } = useStore.getState();
  if (!tl) return null;
  const idx = tl.clips.findIndex((x) => x.id === c.id);
  const start = clipStarts(tl)[idx] ?? 0;
  const dur = clipDur(c);
  const local = Math.max(0, Math.min(dur, playhead - start));
  const inside = playhead >= start - 1e-3 && playhead <= start + dur + 1e-3;
  const kfs = [...(c.keyframes || [])].sort((a, b) => a.t - b.t);
  const setKfs = (next: Keyframe[]) => updateClip(c.id, { keyframes: next.sort((a, b) => a.t - b.t) });
  const near = kfs.findIndex((k) => Math.abs(k.t - local) < 0.05);

  const addKf = () => {
    const cur = kfAt(c, local) ?? { zoom: c.zoom, x: c.focus_x ?? 0.5, y: c.focus_y ?? 0.5 };
    const k: Keyframe = { t: +local.toFixed(3), zoom: +cur.zoom.toFixed(3), x: +cur.x.toFixed(3), y: +cur.y.toFixed(3), ease: "doux" };
    // Premier point posé : on ajoute aussi le début du plan pour que l'animation parte du cadrage actuel.
    const base = !kfs.length && k.t > 0.05 ? [{ ...k, t: 0 }] : [];
    setKfs([...kfs.filter((x) => Math.abs(x.t - k.t) >= 0.05), ...base, k]);
  };
  const edit = (i: number, patch: Partial<Keyframe>) => setKfs(kfs.map((k, j) => (j === i ? { ...k, ...patch } : k)));
  const presets: { label: string; make: () => Keyframe[] }[] = [
    { label: "Zoom avant", make: () => [{ t: 0, zoom: 1, x: 0.5, y: 0.5, ease: "doux" }, { t: +dur.toFixed(3), zoom: 1.35, x: c.focus_x ?? 0.5, y: c.focus_y ?? 0.45, ease: "doux" }] },
    { label: "Zoom arrière", make: () => [{ t: 0, zoom: 1.35, x: c.focus_x ?? 0.5, y: c.focus_y ?? 0.45, ease: "doux" }, { t: +dur.toFixed(3), zoom: 1, x: 0.5, y: 0.5, ease: "doux" }] },
    { label: "Panoramique →", make: () => [{ t: 0, zoom: 1.3, x: 0.2, y: 0.5, ease: "lineaire" }, { t: +dur.toFixed(3), zoom: 1.3, x: 0.8, y: 0.5, ease: "lineaire" }] },
    { label: "Zoom choc", make: () => [{ t: 0, zoom: 1, x: 0.5, y: 0.5, ease: "doux" }, { t: +Math.min(0.25, dur / 3).toFixed(3), zoom: 1.6, x: c.focus_x ?? 0.5, y: c.focus_y ?? 0.45, ease: "doux" }, { t: +Math.min(0.8, dur * 0.66).toFixed(3), zoom: 1, x: 0.5, y: 0.5, ease: "doux" }] },
  ];

  return (
    <div className="fx-section">
      <div className="fx-title">Rampe de vitesse</div>
      <div className="field-row">
        <label>Début
          <select value={c.speed} onChange={(e) => updateClip(c.id, { speed: +e.target.value })}>
            {RAMPS.map((v) => <option key={v} value={v}>×{v}</option>)}
          </select>
        </label>
        <label>Fin
          <select value={c.speed_end ?? ""} onChange={(e) => updateClip(c.id, { speed_end: e.target.value === "" ? null : +e.target.value })}>
            <option value="">Sans rampe</option>
            {RAMPS.map((v) => <option key={v} value={v}>×{v}</option>)}
          </select>
        </label>
      </div>
      <p className="muted small">La vitesse passe progressivement du début à la fin du plan (le son suit, sans changer de hauteur).</p>

      <div className="fx-title">Images clés : zoom et cadrage animés</div>
      <div className="preset-grid">
        {presets.map((p) => <button key={p.label} className="fx-chip" onClick={() => setKfs(p.make())}>{p.label}</button>)}
      </div>
      <div className="field-row">
        <button className="small-btn" disabled={!inside} onClick={addKf} title="Ajouter une image clé à l'endroit du curseur">
          <Icon name="plus" size={13} strokeWidth={2.4} />{near >= 0 ? "Remplacer" : "Ajouter"} au curseur ({fmtTime(local, true)})
        </button>
        {kfs.length > 0 && <button className="small-btn" onClick={() => setKfs([])}>Tout effacer</button>}
      </div>
      <div className="kf-list">
        {kfs.map((k, i) => (
          <div key={i} className={`kf-row ${i === near ? "on" : ""}`}>
            <button className="icon-btn" title="Aller à cette image clé" onClick={() => setPlayhead(start + k.t)} aria-label="Aller à cette image clé"><Icon name="dot" size={14} /></button>
            <div>
              <div className="muted small">{fmtTime(k.t, true)} · {k.ease === "doux" ? "doux" : "linéaire"}</div>
              <div className="kf-fields">
                <label>Zoom {Math.round(k.zoom * 100)} %
                  <input type="range" min={1} max={3} step={0.01} value={k.zoom} onChange={(e) => edit(i, { zoom: +e.target.value })} />
                </label>
                <label>Gauche ↔ droite
                  <input type="range" min={0} max={1} step={0.01} value={k.x} onChange={(e) => edit(i, { x: +e.target.value })} />
                </label>
                <label>Haut ↕ bas
                  <input type="range" min={0} max={1} step={0.01} value={k.y} onChange={(e) => edit(i, { y: +e.target.value })} />
                </label>
              </div>
              <label className="inline small">
                <input type="checkbox" checked={k.ease === "doux"} onChange={(e) => edit(i, { ease: e.target.checked ? "doux" : "lineaire" })} />
                Arrivée douce
              </label>
            </div>
            <button className="icon-btn" title="Supprimer" onClick={() => setKfs(kfs.filter((_, j) => j !== i))} aria-label="Supprimer"><Icon name="x" size={14} /></button>
          </div>
        ))}
      </div>
      {!kfs.length && <p className="muted small">Place le curseur dans le plan, règle le cadre, puis ajoute une image clé : entre deux images clés, le cadre glisse tout seul.</p>}
    </div>
  );
}
