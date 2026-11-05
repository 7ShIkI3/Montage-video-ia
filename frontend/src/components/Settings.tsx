// Réglages : clé Claude, transcription (GPU/CPU, modèle, langue), voix de synthèse et état du système.
import { useEffect, useState } from "react";
import { api, Settings as ServerSettings } from "../api";
import { useStore } from "../store";

const CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"];
const EFFORTS = ["low", "medium", "high", "max"];
const WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"];
const CUDA_COMMAND = "backend\\.venv\\Scripts\\python -m pip install -r backend/requirements-cuda.txt";

const ENGINE_LABEL = { piper: "voix naturelle", espeak: "voix de synthèse" } as const;
const FIELD_LABEL: Record<string, string> = {
  claude_model: "modèle Claude", claude_effort: "effort Claude", whisper_model: "modèle Whisper",
  whisper_device: "calcul Whisper", whisper_language: "langue forcée",
};

/** Avertit que ces champs viennent de l'environnement : ce qui est saisi ici est enregistré mais ignoré. */
function EnvNote({ cfg, fields }: { cfg: ServerSettings; fields: string[] }) {
  const forced = fields.filter((f) => cfg.env_overrides?.includes(f));
  if (!forced.length) return null;
  return (
    <p className="banner">
      Forcé par variable d'environnement : {forced.map((f) => FIELD_LABEL[f] ?? f).join(", ")}. La valeur saisie ici est ignorée tant que la variable est définie.
    </p>
  );
}

/** Liste déroulante qui garde une valeur inconnue du serveur au lieu de la perdre. */
function Choice({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  const list = options.includes(value) ? options : [value, ...options];
  return <select value={value} onChange={(e) => onChange(e.target.value)}>{list.map((o) => <option key={o} value={o}>{o}</option>)}</select>;
}

export function Settings({ onBack }: { onBack: () => void }) {
  const caps = useStore((s) => s.caps);
  const [cfg, setCfg] = useState<ServerSettings | null>(null);
  const [langs, setLangs] = useState<{ code: string; name: string }[]>([]);
  const [key, setKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [device, setDevice] = useState<ServerSettings["whisper_device"]>("auto");
  const [whisper, setWhisper] = useState("");
  const [language, setLanguage] = useState("");
  const [port, setPort] = useState("8000");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; text: string } | null>(null);

  const fill = (c: ServerSettings) => {
    setCfg(c);
    setModel(c.claude_model);
    setEffort(c.claude_effort);
    setDevice(c.whisper_device);
    setWhisper(c.whisper_model);
    setLanguage(c.whisper_language ?? "");
    setPort(String(c.port));
  };

  useEffect(() => {
    const st = useStore.getState();
    st.guard("Chargement des réglages impossible", async () => fill(await api.settings()));
    st.refreshCaps();
    api.languages().then(setLangs).catch(() => undefined);
  }, []);

  /** Envoie un changement partiel, puis recharge l'état du système (la pastille de l'éditeur suit). */
  const save = async (patch: Parameters<typeof api.saveSettings>[0], done: string) => {
    const st = useStore.getState();
    setSaving(true);
    try {
      fill(await api.saveSettings(patch));
      setKey("");
      setTest(null);
      st.notify(done, { level: "ok" });
      await st.refreshCaps();
    } catch (e) {
      st.fail("Enregistrement impossible", e);
    } finally {
      setSaving(false);
    }
  };

  const saveAll = () => save({
    ...(key.trim() ? { anthropic_api_key: key.trim() } : {}),
    claude_model: model, claude_effort: effort, whisper_model: whisper, whisper_device: device,
    whisper_language: language.trim() || null,
    ...(/^\d+$/.test(port.trim()) ? { port: Number(port.trim()) } : {}),
  }, "Réglages enregistrés.");

  const clearKey = () => {
    if (confirm("Effacer la clé Claude enregistrée ?")) save({ anthropic_api_key: "" }, "Clé Claude effacée.");
  };

  const testKey = async () => {
    setTesting(true);
    setTest(null);
    try {
      const r = await api.testClaude();
      setTest(r.ok ? { ok: true, text: `OK · ${r.model}` } : { ok: false, text: r.error || "Échec du test" });
    } catch (e) {
      setTest({ ok: false, text: (e as Error).message });
    } finally {
      setTesting(false);
    }
  };

  const copy = (t: string) => navigator.clipboard?.writeText(t).then(
    () => useStore.getState().notify("Copié !"),
    () => useStore.getState().notify("Copie impossible : sélectionne la commande à la main."));

  const langName = (code: string) => langs.find((l) => l.code === code)?.name ?? code;
  const source = cfg?.key_source === "env" ? "variable d'environnement" : "fichier";

  return (
    <div className="home settings-page">
      <header className="settings-head">
        <button className="icon-btn" onClick={onBack} title="Retour">← Retour</button>
        <h1>Réglages</h1>
      </header>
      {!cfg ? <p className="muted">Chargement…</p> : (
        <>
          <section className="panel settings-section">
            <header><h3>Claude</h3></header>
            <p className="small">
              {cfg.anthropic_api_key_set
                ? <>Clé définie{cfg.anthropic_api_key_hint ? ` (…${cfg.anthropic_api_key_hint.replace(/^[….*•]+/, "")})` : ""} via {source}.</>
                : <span className="warn">Aucune clé : le montage utilise l'IA locale, plus simple.</span>}
            </p>
            {cfg.key_source === "env" && (
              <p className="banner">La clé vient de la variable d'environnement ANTHROPIC_API_KEY : elle a priorité sur celle saisie ici.</p>
            )}
            <label>Clé API Anthropic
              <div className="key-row">
                <input type={showKey ? "text" : "password"} value={key} placeholder="sk-ant-…" autoComplete="off" spellCheck={false}
                  onChange={(e) => setKey(e.target.value)} />
                <button type="button" className="small-btn" onClick={() => setShowKey(!showKey)}
                  title={showKey ? "Masquer la clé" : "Afficher la clé"} aria-label={showKey ? "Masquer la clé" : "Afficher la clé"}>
                  {showKey ? "🙈" : "👁"}
                </button>
              </div>
            </label>
            <EnvNote cfg={cfg} fields={["claude_model", "claude_effort"]} />
            <div className="field-row">
              <label>Modèle<Choice value={model} options={CLAUDE_MODELS} onChange={setModel} /></label>
              <label>Effort de réflexion<Choice value={effort} options={EFFORTS} onChange={setEffort} /></label>
            </div>
            <div className="row-btns">
              <button className="primary" disabled={saving} onClick={saveAll}>{saving ? "Enregistrement…" : "Enregistrer"}</button>
              <button disabled={testing || !!key.trim() || !cfg.anthropic_api_key_set} onClick={testKey}
                title={key.trim() ? "Enregistre d'abord la clé saisie" : undefined}>{testing ? "Test…" : "Tester"}</button>
              <button disabled={saving || cfg.key_source !== "fichier"} onClick={clearKey}
                title={cfg.key_source === "env" ? "La clé vient de l'environnement : à retirer là-bas" : undefined}>Effacer</button>
              {test && <span className={`small ${test.ok ? "test-ok" : "warn"}`}>{test.text}</span>}
            </div>
            <p className="muted small">Stockée dans Documents/Monteur IA/settings.json, jamais dans le dépôt ni envoyée ailleurs qu'à Anthropic.</p>
          </section>

          <section className="panel settings-section">
            <header><h3>Transcription</h3></header>
            {caps?.whisper_fallback && (
              <p className="banner warn">GPU indisponible : {caps.whisper_fallback} — transcription sur CPU</p>
            )}
            {caps?.cuda && !caps.cublas && (
              <div className="banner">
                <p>Une carte NVIDIA est détectée, mais la bibliothèque cuBLAS manque. Pour l'installer :</p>
                <div className="key-row">
                  <code className="cmd">{CUDA_COMMAND}</code>
                  <button type="button" className="small-btn" onClick={() => copy(CUDA_COMMAND)}>Copier</button>
                </div>
              </div>
            )}
            <EnvNote cfg={cfg} fields={["whisper_model", "whisper_device", "whisper_language"]} />
            <div className="field-row">
              <label>Calcul
                <select value={device} onChange={(e) => setDevice(e.target.value as ServerSettings["whisper_device"])}>
                  <option value="auto">Auto</option>
                  <option value="cuda">GPU NVIDIA (cuda)</option>
                  <option value="cpu">CPU</option>
                </select>
              </label>
              <label>Modèle Whisper<Choice value={whisper} options={WHISPER_MODELS} onChange={setWhisper} /></label>
            </div>
            <p className="muted small">Auto prend le GPU NVIDIA s'il fonctionne, sinon le processeur. Le GPU transcrit bien plus vite ; le CPU marche partout. Un modèle plus gros comprend mieux mais est plus lent.</p>
            <label>Langue forcée <span className="muted small">(vide = détection automatique)</span>
              <input value={language} placeholder="fr, en, es…" maxLength={8} onChange={(e) => setLanguage(e.target.value)} />
            </label>
            <div className="row-btns">
              <button className="primary" disabled={saving} onClick={saveAll}>{saving ? "Enregistrement…" : "Enregistrer"}</button>
            </div>
          </section>

          <section className="panel settings-section">
            <header><h3>Voix</h3></header>
            {caps && Object.keys(caps.tts ?? {}).length ? (
              <table className="settings-table">
                <thead><tr><th>Langue</th><th>Moteur</th></tr></thead>
                <tbody>
                  {Object.entries(caps.tts).map(([code, engine]) => (
                    <tr key={code}>
                      <td>{langName(code)}</td>
                      <td className={engine ? "" : "muted"}>{engine ? `${engine} · ${ENGINE_LABEL[engine]}` : "aucune"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <p className="muted small">État des voix indisponible.</p>}
            <p className="muted small">Les voix servent au doublage et à la voix off IA. Une voix Piper sonne naturelle ; espeak est une voix de secours.</p>
          </section>

          <section className="panel settings-section">
            <header><h3>Serveur</h3></header>
            <label>Port de l'application de bureau
              <input type="number" min={1024} max={65535} value={port} onChange={(e) => setPort(e.target.value)} />
            </label>
            <p className="muted small">
              Pris en compte au prochain lancement de l'application de bureau (si le port est occupé, un port libre est choisi).
              Le serveur lancé à la main avec uvicorn garde son option <code>--port</code>.
            </p>
            <div className="row-btns">
              <button className="primary" disabled={saving} onClick={saveAll}>{saving ? "Enregistrement…" : "Enregistrer"}</button>
            </div>
          </section>

          <section className="panel settings-section">
            <header><h3>Système</h3></header>
            {caps ? (
              <ul className="settings-list small">
                <li>Encodeur vidéo : <b>{caps.encoder}</b></li>
                <li>Whisper : <b>{caps.whisper_model}</b>{caps.whisper_device ? ` sur ${caps.whisper_device === "cuda" ? "GPU" : "CPU"}` : ""}</li>
                <li>Réalisateur : <b>{caps.claude ? caps.claude_model : "IA locale (pas de clé Claude)"}</b></li>
                <li>Bibliothèque musicale : <b>{caps.music_library}</b> morceau{caps.music_library > 1 ? "x" : ""}</li>
              </ul>
            ) : <p className="muted small">Le serveur ne répond pas.</p>}
          </section>
        </>
      )}
    </div>
  );
}
