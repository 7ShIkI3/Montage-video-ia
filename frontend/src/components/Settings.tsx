// Réglages : clé Claude, transcription (GPU/CPU, modèle, langue), voix de synthèse, serveur et état du système.
import { useEffect, useState } from "react";
import { api, Job, ModelsStatus, PiperVoice, Settings as ServerSettings } from "../api";
import { Icon } from "../icons";
import { useStore } from "../store";
import { JobLine, runStandaloneJob } from "./Library";
import { ask } from "./Dialog";
import { SystemBar } from "./SystemBar";

const PIPER_LANGS = [["fr", "Français"], ["en", "Anglais"], ["es", "Espagnol"], ["de", "Allemand"], ["it", "Italien"], ["pt", "Portugais"], ["nl", "Néerlandais"], ["pl", "Polonais"], ["tr", "Turc"], ["ru", "Russe"], ["ar", "Arabe"], ["zh", "Chinois"]];

/** Modèles Whisper : lesquels sont déjà sur le disque, téléchargement à l'avance. */
function WhisperModels({ onChanged }: { onChanged: () => void }) {
  const [models, setModels] = useState<ModelsStatus["whisper"] | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [which, setWhich] = useState<string | null>(null);
  const refresh = () => api.models().then((m) => setModels(m.whisper)).catch(() => setModels([]));
  useEffect(() => { refresh(); }, []);
  const download = async (size: string) => {
    setWhich(size);
    const done = await runStandaloneJob(`Whisper ${size}`, () => api.downloadWhisper(size), setJob);
    setJob(null);
    setWhich(null);
    if (done) { useStore.getState().notify(`Whisper ${size} téléchargé.`, { level: "ok" }); await refresh(); onChanged(); }
  };
  if (!models) return <p className="muted small">État des modèles…</p>;
  return (
    <div>
      <table className="settings-table model-table">
        <thead><tr><th>Modèle</th><th className="right">Taille</th><th className="right">État</th></tr></thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.size}>
              <td>{m.size}{m.current ? <span className="muted small"> · en service</span> : ""}</td>
              <td className="right">{m.approx_mb >= 1000 ? `${(m.approx_mb / 1000).toFixed(1)} Go` : `${m.approx_mb} Mo`}</td>
              <td className="right">{m.downloaded ? <span className="ok"><Icon name="check" size={13} strokeWidth={3} /> téléchargé</span>
                : <button className="small-btn" disabled={!!which} onClick={() => download(m.size)}>{which === m.size ? "…" : "Télécharger"}</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <JobLine job={job} />
      <p className="muted small" style={{ margin: "6px 0 0" }}>Un modèle absent est téléchargé au premier usage ; le faire ici évite d'attendre pendant un montage. Les modèles viennent de Hugging Face (Systran/faster-whisper).</p>
    </div>
  );
}

/** Voix Piper par langue : catalogue Hugging Face, téléchargement dans le dossier des voix. */
function PiperVoices({ runtime, onChanged }: { runtime: boolean; onChanged: () => void }) {
  const [lang, setLang] = useState("fr");
  const [voices, setVoices] = useState<PiperVoice[] | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [which, setWhich] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = (l = lang) => { setVoices(null); setError(null); api.piperCatalog(l).then(setVoices).catch((e) => { setError((e as Error).message); setVoices([]); }); };
  useEffect(() => { refresh(lang); }, [lang]);  // eslint-disable-line
  const download = async (v: PiperVoice) => {
    setWhich(v.key);
    const done = await runStandaloneJob(`Voix ${v.name}`, () => api.downloadPiper(v.key), setJob);
    setJob(null);
    setWhich(null);
    if (done) { useStore.getState().notify(`Voix ${v.key} installée.`, { level: "ok" }); refresh(); onChanged(); }
  };
  return (
    <div>
      <div className="field-row" style={{ gridTemplateColumns: "200px 1fr", alignItems: "end" }}>
        <label>Langue<select value={lang} onChange={(e) => setLang(e.target.value)}>{PIPER_LANGS.map(([c, n]) => <option key={c} value={c}>{n}</option>)}</select></label>
        <p className="muted small" style={{ margin: 0 }}>{runtime ? "Piper est prêt : une voix téléchargée est utilisée dès le prochain doublage ou voix de synthèse." : "Piper n'est pas installé dans cette version : les voix téléchargées serviront une fois Piper présent."}</p>
      </div>
      {error && <p className="warn small">{error}</p>}
      {voices === null ? <p className="muted small">Catalogue…</p> : !voices.length ? <p className="muted small">Aucune voix pour cette langue.</p> : (
        <table className="settings-table model-table">
          <thead><tr><th>Voix</th><th>Qualité</th><th className="right">Taille</th><th className="right">État</th></tr></thead>
          <tbody>
            {voices.map((v) => (
              <tr key={v.key}>
                <td>{v.name}{v.region ? <span className="muted small"> · {v.region}</span> : ""}{v.speakers > 1 ? <span className="muted small"> · {v.speakers} voix</span> : ""}</td>
                <td>{v.quality}</td>
                <td className="right">{v.approx_mb ? `${v.approx_mb} Mo` : "—"}</td>
                <td className="right">{v.installed ? <span className="ok"><Icon name="check" size={13} strokeWidth={3} /> installée</span>
                  : <button className="small-btn" disabled={!!which} onClick={() => download(v)}>{which === v.key ? "…" : "Télécharger"}</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <JobLine job={job} />
    </div>
  );
}

const CLAUDE_MODELS = ["claude-opus-5-5", "claude-fable-5-2", "claude-sonnet-5-5", "claude-haiku-5-5"];
const EFFORTS = ["low", "medium", "high", "xhigh", "max"];
const WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"];
const CUDA_COMMAND = "backend\\.venv\\Scripts\\python -m pip install -r backend/requirements-cuda.txt";

const ENGINE_LABEL = { piper: "voix naturelle", espeak: "voix de synthèse" } as const;
const FIELD_LABEL: Record<string, string> = {
  claude_model: "modèle Claude", claude_effort: "effort Claude", whisper_model: "modèle Whisper",
  whisper_device: "calcul Whisper", whisper_language: "langue forcée",
};
const SECTIONS = [
  { id: "claude", label: "Claude" }, { id: "transcription", label: "Transcription" }, { id: "voix", label: "Voix" },
  { id: "serveur", label: "Serveur" }, { id: "systeme", label: "Système" },
];

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
  const [section, setSection] = useState("claude");
  const [piperRuntime, setPiperRuntime] = useState(false);
  const [voicesDir, setVoicesDir] = useState("");
  useEffect(() => { api.models().then((m) => { setPiperRuntime(m.piper.runtime); setVoicesDir(m.piper.voices_dir); }).catch(() => undefined); }, []);

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

  // La navigation suit la section visible.
  useEffect(() => {
    const els = SECTIONS.map((s) => document.getElementById(s.id)).filter((x): x is HTMLElement => !!x);
    if (!els.length) return;
    const obs = new IntersectionObserver((entries) => {
      const seen = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (seen[0]) setSection(seen[0].target.id);
    }, { rootMargin: "-80px 0px -60% 0px" });
    els.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, [cfg]);

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

  const clearKey = async () => {
    if (await ask.confirm("Effacer la clé Claude enregistrée ?", { title: "Effacer la clé", ok: "Effacer", danger: true })) save({ anthropic_api_key: "" }, "Clé Claude effacée.");
  };

  const testKey = async () => {
    setTesting(true);
    setTest(null);
    try {
      const r = await api.testClaude();
      setTest(r.ok ? { ok: true, text: `Connexion OK · ${r.model}` } : { ok: false, text: r.error || "Échec du test" });
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
    <div className="settings-page">
      <header className="appbar">
        <button className="back-link" onClick={onBack}><Icon name="back" size={18} strokeWidth={2} />Retour</button>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Réglages</h1>
        <span className="spacer" />
        <span className="muted small">Enregistrés dans Documents/Monteur IA/settings.json</span>
      </header>
      {!cfg ? <p className="muted" style={{ padding: 32 }}>Chargement…</p> : (
        <div className="settings-wrap">
          <nav className="settings-nav" aria-label="Sections des réglages">
            {SECTIONS.map((s) => <a key={s.id} href={`#/reglages#${s.id}`} className={section === s.id ? "on" : ""}
              onClick={(e) => { e.preventDefault(); document.getElementById(s.id)?.scrollIntoView({ behavior: "smooth", block: "start" }); setSection(s.id); }}>{s.label}</a>)}
          </nav>
          <div className="settings-body">
            <section id="claude" className="settings-section">
              <header>
                <div>
                  <h3>Claude</h3>
                  <p>Le réalisateur : consignes libres, traduction, doublage, recherche. Sans clé, l'app utilise le moteur local, plus simple.</p>
                </div>
                {cfg.anthropic_api_key_set
                  ? <span className="status-chip ok">Clé définie{cfg.anthropic_api_key_hint ? ` · …${cfg.anthropic_api_key_hint.replace(/^[….*•]+/, "")}` : ""} · {source}</span>
                  : <span className="status-chip warn">Aucune clé</span>}
              </header>
              {cfg.key_source === "env" && (
                <p className="banner">La clé vient de la variable d'environnement ANTHROPIC_API_KEY : elle a priorité sur celle saisie ici.</p>
              )}
              <label>Clé API Anthropic
                <div className="key-row">
                  <input type={showKey ? "text" : "password"} value={key} placeholder="sk-ant-…" autoComplete="off" spellCheck={false}
                    onChange={(e) => setKey(e.target.value)} />
                  <button type="button" className="small-btn" onClick={() => setShowKey(!showKey)}
                    title={showKey ? "Masquer la clé" : "Afficher la clé"} aria-label={showKey ? "Masquer la clé" : "Afficher la clé"}>
                    <Icon name={showKey ? "eye-off" : "eye"} size={16} />
                  </button>
                </div>
              </label>
              <EnvNote cfg={cfg} fields={["claude_model", "claude_effort"]} />
              <div className="field-row">
                <label>Modèle<Choice value={model} options={CLAUDE_MODELS} onChange={setModel} /></label>
                <label>Effort de réflexion<Choice value={effort} options={EFFORTS} onChange={setEffort} /></label>
              </div>
              <div className="row-btns">
                <button disabled={testing || !!key.trim() || !cfg.anthropic_api_key_set} onClick={testKey}
                  title={key.trim() ? "Enregistre d'abord la clé saisie" : undefined}>{testing ? "Test…" : "Tester la clé"}</button>
                <button className="danger" disabled={saving || cfg.key_source !== "fichier"} onClick={clearKey}
                  title={cfg.key_source === "env" ? "La clé vient de l'environnement : à retirer là-bas" : undefined}>Effacer</button>
                {test && <span className={`small ${test.ok ? "test-ok" : "warn"}`}>{test.ok && <Icon name="check" size={14} strokeWidth={3} />}{test.text}</span>}
              </div>
              <p className="muted small" style={{ margin: 0 }}>Jamais dans le dépôt, jamais envoyée ailleurs qu'à Anthropic. Une variable d'environnement ANTHROPIC_API_KEY garde la priorité.</p>
            </section>

            <section id="transcription" className="settings-section">
              <header>
                <div>
                  <h3>Transcription</h3>
                  <p>Whisper transcrit les rushes au mot près. Le GPU va bien plus vite ; le processeur marche partout.</p>
                </div>
              </header>
              {caps?.whisper_fallback && (
                <div className="banner warn"><Icon name="alert" size={18} /><div><p style={{ margin: 0 }}>GPU indisponible : {caps.whisper_fallback}. Transcription sur CPU pour l'instant.</p></div></div>
              )}
              {caps?.cuda && !caps.cublas && (
                <div className="banner warn">
                  <Icon name="alert" size={18} />
                  <div>
                    <p>Carte NVIDIA détectée, mais la bibliothèque cuBLAS manque. Pour l'installer :</p>
                    <div className="key-row">
                      <code className="cmd">{CUDA_COMMAND}</code>
                      <button type="button" className="small-btn" style={{ width: "auto", padding: "0 10px" }} onClick={() => copy(CUDA_COMMAND)}><Icon name="copy" size={14} />Copier</button>
                    </div>
                  </div>
                </div>
              )}
              <EnvNote cfg={cfg} fields={["whisper_model", "whisper_device", "whisper_language"]} />
              <div className="field-row three">
                <label>Calcul
                  <select value={device} onChange={(e) => setDevice(e.target.value as ServerSettings["whisper_device"])}>
                    <option value="auto">Auto</option>
                    <option value="cuda">GPU NVIDIA (cuda)</option>
                    <option value="cpu">CPU</option>
                  </select>
                </label>
                <label>Modèle Whisper<Choice value={whisper} options={WHISPER_MODELS} onChange={setWhisper} /></label>
                <label>Langue forcée
                  <input value={language} placeholder="auto (fr, en, es…)" maxLength={8} onChange={(e) => setLanguage(e.target.value)} />
                </label>
              </div>
              <p className="muted small" style={{ margin: 0 }}>Auto prend le GPU NVIDIA s'il fonctionne, sinon le processeur. Un modèle plus gros comprend mieux mais est plus lent. Langue vide : détection automatique.</p>
              <h4 style={{ margin: "18px 0 8px" }}>Modèles sur le disque</h4>
              <WhisperModels onChanged={() => useStore.getState().refreshCaps()} />
            </section>

            <section id="voix" className="settings-section">
              <header>
                <div>
                  <h3>Voix</h3>
                  <p>Pour le doublage et la voix de synthèse. Piper sonne naturel, espeak est une voix de secours.</p>
                </div>
              </header>
              {caps && Object.keys(caps.tts ?? {}).length ? (
                <div style={{ overflowX: "auto" }}>
                  <table className="settings-table">
                    <thead><tr><th>Langue</th><th>Moteur</th><th style={{ textAlign: "right" }}>Qualité</th></tr></thead>
                    <tbody>
                      {Object.entries(caps.tts).map(([code, engine]) => (
                        <tr key={code}>
                          <td>{langName(code)}</td>
                          <td className={engine ? "" : "muted"}>{engine ?? "aucune"}</td>
                          <td style={{ textAlign: "right" }} className={engine === "piper" ? "" : engine ? "warn" : "muted"}>{engine ? ENGINE_LABEL[engine] : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <p className="muted small">État des voix indisponible.</p>}
              <h4 style={{ margin: "18px 0 8px" }}>Télécharger une voix naturelle (Piper)</h4>
              <PiperVoices runtime={piperRuntime} onChanged={() => useStore.getState().refreshCaps()} />
              <p className="muted small" style={{ margin: 0 }}>Les voix sont rangées dans {voicesDir || "le dossier des voix"} ; tu peux aussi y déposer un fichier <code>&lt;langue&gt;*.onnx</code> avec son <code>.json</code>.</p>
            </section>

            <section id="serveur" className="settings-section">
              <header>
                <div>
                  <h3>Serveur</h3>
                  <p>Pris en compte au prochain lancement de l'application de bureau. Si le port est occupé, un port libre est choisi.</p>
                </div>
              </header>
              <label style={{ width: 200 }}>Port
                <input type="number" min={1024} max={65535} value={port} onChange={(e) => setPort(e.target.value)} />
              </label>
            </section>

            <section id="systeme" className="settings-section">
              <header><div><h3>Système</h3></div></header>
              <SystemBar />
            </section>
            <div className="settings-save">
              <span className="muted small">Un seul bouton pour toutes les sections : clé, modèle, transcription et port.</span>
              <button className="primary" disabled={saving} onClick={saveAll}>{saving ? "Enregistrement…" : "Enregistrer"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
