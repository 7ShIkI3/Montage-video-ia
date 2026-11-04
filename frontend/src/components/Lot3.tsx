// Lot 3 : étalonnage par LUT, détourage IA sans fond vert, doublage IA, et aperçu image fixe du rendu.
import { useEffect, useRef, useState } from "react";
import { api, Clip, waitJob } from "../api";
import { clipAt, clipStarts, localToSrc, useStore } from "../store";

type Lut = { id: string; label: string; builtin: boolean };
export const CUTOUTS = ["detourage_flou", "detourage_studio", "detourage_noir", "detourage_blanc", "detourage_image"];

/** Rush et instant source sous le curseur (ou au début du plan donné), pour l'aperçu image fixe. */
function sourceAtPlayhead(c?: Clip): { media: string; t: number } | null {
  const { timeline: tl, playhead } = useStore.getState();
  if (!tl || !tl.clips.length) return null;
  let i = c ? tl.clips.findIndex((x) => x.id === c.id) : clipAt(tl, playhead)?.index ?? 0;
  if (i < 0) i = 0;
  const cl = tl.clips[i];
  const local = Math.max(0, playhead - (clipStarts(tl)[i] ?? 0));
  return { media: cl.media_id, t: cl.src_in + localToSrc(cl, local) };
}

function Preview({ url, onClose }: { url: string; onClose: () => void }) {
  const [state, setState] = useState<"load" | "ok" | "err">("load");
  return (
    <div className="still-preview">
      {state === "load" && <p className="muted small">Calcul de l'aperçu…</p>}
      {state === "err" && <p className="muted small">Aperçu impossible (modèle de détourage absent ?).</p>}
      <img src={url} alt="Aperçu du rendu" style={{ display: state === "ok" ? "block" : "none" }}
        onLoad={() => setState("ok")} onError={() => setState("err")} />
      <button className="small-btn" onClick={onClose}>Fermer l'aperçu</button>
    </div>
  );
}

/** Étalonnage par LUT 3D : 5 LUT intégrées, import d'un fichier .cube, intensité réglable. */
export function LutPicker() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const [list, setList] = useState<Lut[]>([]);
  const [preview, setPreview] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (project) api.luts(project.id).then(setList).catch(() => undefined); }, [project?.id]);
  if (!project || !tl) return null;
  const { setTimelineProps, notify } = useStore.getState();
  const strength = tl.lut_strength ?? 1;
  const show = () => {
    const src = sourceAtPlayhead();
    if (src) setPreview(api.previewFrame(project.id, { ...src, lut: tl.lut, strength }));
  };
  return (
    <div className="fx-section">
      <div className="fx-title">Étalonnage LUT</div>
      <div className="field-row">
        <label>LUT
          <select value={tl.lut || ""} onChange={(e) => { setTimelineProps({ lut: e.target.value || null }); setPreview(null); }}>
            <option value="">Aucune</option>
            {list.map((l) => <option key={l.id} value={l.id}>{l.builtin ? l.label : `${l.label} (importée)`}</option>)}
          </select>
        </label>
        <button className="small-btn" onClick={() => input.current?.click()}>Importer .cube…</button>
        <input ref={input} type="file" accept=".cube" hidden onChange={async (e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (!f) return;
          try {
            const l = await api.uploadLut(project.id, f);
            setList(await api.luts(project.id));
            setTimelineProps({ lut: l.id });
            notify(`LUT « ${l.label} » importée.`);
          } catch (err) { notify(`LUT refusée : ${(err as Error).message}`); }
        }} />
      </div>
      {tl.lut && (
        <>
          <label>Intensité {Math.round(strength * 100)} %
            <input type="range" min={0} max={1} step={0.05} value={strength}
              onChange={(e) => setTimelineProps({ lut_strength: +e.target.value })} />
          </label>
          <button className="small-btn" onClick={show}>Voir l'aperçu au curseur</button>
          <p className="muted small">La LUT s'applique à l'export ; l'aperçu image fixe montre le rendu final.</p>
        </>
      )}
      {preview && <Preview key={preview} url={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

/** Réglages du détourage IA d'un plan : fond image et aperçu. */
export function CutoutTools({ c }: { c: Clip }) {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const [preview, setPreview] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const kind = c.effects.find((e) => CUTOUTS.includes(e));
  if (!project || !tl || !kind) return null;
  const { setTimelineProps, notify } = useStore.getState();
  const show = () => {
    const src = sourceAtPlayhead(c);
    if (src) setPreview(api.previewFrame(project.id, { ...src, effect: kind, background: tl.background }));
  };
  return (
    <div className="cutout-box">
      <p className="muted small">L'IA détoure la personne image par image, sans fond vert. Le fond change à l'export.</p>
      {kind === "detourage_image" && (
        <div className="field-row">
          <span className="small">Fond : {tl.background || "aucune image"}</span>
          <button className="small-btn" onClick={() => input.current?.click()}>Choisir une image…</button>
          <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={async (e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f) return;
            try {
              const r = await api.uploadAsset(project.id, f);
              setTimelineProps({ background: r.asset });
            } catch (err) { notify(`Image refusée : ${(err as Error).message}`); }
          }} />
        </div>
      )}
      <button className="small-btn" onClick={show}>Voir l'aperçu du détourage</button>
      {preview && <Preview key={preview} url={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

/** Doublage IA : traduit les phrases, les fait lire par une voix de synthèse et baisse la voix d'origine. */
export function DubPanel() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const job = useStore((s) => s.job);
  const [langs, setLangs] = useState<{ code: string; name: string }[]>([]);
  const [voices, setVoices] = useState<Record<string, string | null>>({});
  const [lang, setLang] = useState("en");
  const [vol, setVol] = useState(0.12);
  useEffect(() => {
    api.languages().then(setLangs).catch(() => undefined);
    api.ttsStatus().then(setVoices).catch(() => undefined);
  }, []);
  if (!project || !tl) return null;
  const busy = job?.kind === "doublage" && job.status !== "termine" && job.status !== "erreur";
  const run = async (code: string | null) => {
    const st = useStore.getState();
    try {
      await st.saveNow();
      await waitJob(await api.dub(project.id, code, vol), st.setJob);
      await st.refreshProject({ timeline: true });
      st.notify(code ? `Doublage en ${langs.find((l) => l.code === code)?.name ?? code} posé sur la piste audio 4.` : "Voix d'origine rétablie.");
    } catch (e) {
      st.notify(`Doublage impossible : ${(e as Error).message}`);
    } finally { st.setJob(null); }
  };
  const engine = voices[lang];
  const current = tl.dub_language ? langs.find((l) => l.code === tl.dub_language)?.name ?? tl.dub_language : null;
  return (
    <div className="fx-section">
      <div className="fx-title">Doublage IA</div>
      {current && <p className="small">Doublé en <b>{current}</b> · {(tl.audio ?? []).filter((a) => a.dub).length} phrase(s)</p>}
      <div className="field-row">
        <label>Langue
          <select value={lang} onChange={(e) => setLang(e.target.value)}>
            {langs.map((l) => <option key={l.code} value={l.code}>{l.name}{voices[l.code] ? "" : " (pas de voix)"}</option>)}
          </select>
        </label>
        <label>Voix d'origine {Math.round(vol * 100)} %
          <input type="range" min={0} max={0.6} step={0.02} value={vol} onChange={(e) => setVol(+e.target.value)} />
        </label>
      </div>
      <div className="field-row">
        <button className="small-btn primary" disabled={busy || !engine} onClick={() => run(lang)}>
          {busy ? "Doublage…" : current ? "Refaire le doublage" : "Doubler la vidéo"}
        </button>
        {current && <button className="small-btn" disabled={busy} onClick={() => run(null)}>Retirer le doublage</button>}
      </div>
      <p className="muted small">
        {engine === "piper" ? "Voix naturelle Piper." : engine === "espeak" ? "Voix de synthèse simple (espeak). Ajoute une voix Piper pour un rendu naturel." : "Aucune voix installée pour cette langue."}
        {" "}Chaque phrase est traduite par Claude puis calée sur l'instant où elle est dite.
      </p>
    </div>
  );
}

/** Voix off IA : un texte lu par une voix de synthèse, réécouté, calé au mot près (sous-titres compris). */
export function AIVoiceoverPanel() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const playhead = useStore((s) => s.playhead);
  const [text, setText] = useState("");
  const [lang, setLang] = useState("fr");
  const [langs, setLangs] = useState<{ code: string; name: string }[]>([]);
  const [voices, setVoices] = useState<Record<string, string | null>>({});
  useEffect(() => {
    api.languages().then(setLangs).catch(() => undefined);
    api.ttsStatus().then(setVoices).catch(() => undefined);
  }, []);
  if (!project) return null;
  const busy = job?.kind === "voix_ia" && job.status !== "termine" && job.status !== "erreur";
  const run = async () => {
    const st = useStore.getState();
    try {
      await st.saveNow();
      const done = await waitJob(await api.voiceover(project.id, text, playhead, lang), st.setJob);
      await st.refreshProject({ timeline: true });
      st.notify(done.message || "Voix off IA posée.");
      setText("");
    } catch (e) {
      st.notify(`Voix off IA impossible : ${(e as Error).message}`);
    } finally { st.setJob(null); }
  };
  return (
    <div className="fx-section">
      <div className="fx-title">Voix off IA</div>
      <textarea rows={3} placeholder="Texte à lire, par exemple : Et maintenant, le moment que vous attendiez."
        value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} />
      <div className="field-row">
        <label>Langue
          <select value={lang} onChange={(e) => setLang(e.target.value)}>
            {langs.map((l) => <option key={l.code} value={l.code}>{l.name}{voices[l.code] ? "" : " (pas de voix)"}</option>)}
          </select>
        </label>
        <button className="small-btn primary" disabled={busy || !text.trim() || !voices[lang]} onClick={run}>
          {busy ? "Lecture…" : `Poser à ${playhead.toFixed(1)} s`}
        </button>
      </div>
      <p className="muted small">La voix est réécoutée : si elle se comprend mal, le contrôle qualité le signale. Chaque mot est horodaté, les sous-titres animés la suivent.</p>
    </div>
  );
}
