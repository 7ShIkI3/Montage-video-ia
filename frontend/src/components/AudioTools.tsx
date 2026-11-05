// Lot 2 : voix off au micro, sons importés sur les pistes audio, et langue des sous-titres.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { CapHint, needClaude } from "../caps";
import { isBusy, useStore } from "../store";
import { Icon } from "../icons";

/** Enregistre une voix off pendant que la vidéo défile depuis le curseur, puis la pose sur une piste audio. */
export function VoiceOverButton() {
  const project = useStore((s) => s.project);
  const [rec, setRec] = useState<{ mr: MediaRecorder; start: number; t0: number } | null>(null);
  const [secs, setSecs] = useState(0);
  const chunks = useRef<Blob[]>([]);
  useEffect(() => {
    if (!rec) return;
    const id = window.setInterval(() => setSecs((performance.now() - rec.t0) / 1000), 200);
    return () => window.clearInterval(id);
  }, [rec]);
  if (!project) return null;
  const { notify, setPlaying, addAudio } = useStore.getState();

  const start = async () => {
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch {
      return notify("Micro inaccessible : autorise le micro pour ce site dans ton navigateur.");
    }
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"].find((m) => MediaRecorder.isTypeSupported(m));
    const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    chunks.current = [];
    mr.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    const at = useStore.getState().playhead;
    mr.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setPlaying(false);
      const blob = new Blob(chunks.current, { type: mr.mimeType || "audio/webm" });
      if (blob.size < 2000) return notify("Enregistrement trop court.");
      try {
        const ext = (mr.mimeType || "").includes("ogg") ? "ogg" : "webm";
        const { asset, duration } = await api.uploadAudio(project.id, blob, `voix-off.${ext}`);
        addAudio(asset, duration, "voix", at, "Voix off");
        notify(`Voix off ajoutée (${duration.toFixed(1)} s).`);
      } catch (e) {
        notify(`Voix off non enregistrée : ${(e as Error).message}`);
      }
    };
    mr.start(250);
    setSecs(0);
    setRec({ mr, start: at, t0: performance.now() });
    setPlaying(true);
  };
  const stop = () => {
    rec?.mr.stop();
    setRec(null);
  };
  return rec ? (
    <button className="rec on" onClick={stop} title="Arrêter l'enregistrement"><Icon name="square" size={12} strokeWidth={2.6} />{secs.toFixed(0)} s</button>
  ) : (
    <button className="rec" onClick={start} title="Enregistrer une voix off au micro, depuis le curseur"><Icon name="mic" size={14} />Voix off</button>
  );
}

/** Importe un son (bruitage, musique, voix enregistrée ailleurs) et le pose au curseur. */
export function AddSoundButton() {
  const project = useStore((s) => s.project);
  const input = useRef<HTMLInputElement>(null);
  if (!project) return null;
  const { notify, addAudio } = useStore.getState();
  return (
    <>
      <input ref={input} type="file" accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac,.webm" hidden onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        try {
          const { asset, duration } = await api.uploadAudio(project.id, f, f.name);
          addAudio(asset, duration, "son", undefined, f.name.replace(/\.[^.]+$/, ""));
        } catch (err) { notify((err as Error).message); }
      }} />
      <button onClick={() => input.current?.click()} title="Importer un son ou une musique sur une piste audio"><Icon name="music" size={14} />Son</button>
    </>
  );
}

/** Langue des sous-titres : la langue parlée, ou une traduction faite par l'IA (gardée en cache). */
export function CaptionLanguage() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const busy = useStore((s) => isBusy(s, "traduction"));
  const reason = useStore((s) => needClaude(s.caps));
  const [langs, setLangs] = useState<{ code: string; name: string }[]>([]);
  useEffect(() => { api.languages().then(setLangs).catch(() => undefined); }, []);
  if (!project || !tl) return null;
  const change = (code: string) => useStore.getState().runJob("Traduction", () => api.translate(project.id, code || null), {
    timeline: true,
    onDone: () => useStore.getState().notify(code ? `Sous-titres traduits (${langs.find((l) => l.code === code)?.name ?? code}).` : "Sous-titres dans la langue parlée."),
  });
  // Sans Claude, on peut seulement revenir à la langue parlée : les traductions sont grisées.
  return (
    <>
      <label>Langue des sous-titres
        <select value={tl.caption_language || ""} disabled={busy || tl.captions === "aucun"} onChange={(e) => change(e.target.value)}>
          <option value="">Langue parlée (originale)</option>
          {langs.map((l) => <option key={l.code} value={l.code} disabled={!!reason}>Traduire en {l.name}</option>)}
        </select>
      </label>
      <CapHint reason={reason} />
    </>
  );
}
