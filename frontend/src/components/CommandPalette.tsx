// Palette de commandes (Ctrl+K) : toute action de l'éditeur au clavier ; sans correspondance, le texte devient une consigne de montage.
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Icon, IconName } from "../icons";
import { useStore } from "../store";
import { Kbd } from "./Shortcuts";

interface Command {
  id: string;
  label: string;
  group: string;
  keys?: string[];
  icon?: IconName;
  /** Mots-clés supplémentaires pour la recherche. */
  alt?: string;
  run: () => void;
  enabled?: () => boolean;
}

/** Sans accents ni majuscules, pour une recherche tolérante. */
const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

function commands(): Command[] {
  const s = useStore.getState();
  const clip = () => (s.item?.kind === "clip" ? s.item.id : null);
  const hasClips = () => !!s.timeline?.clips.length;
  return [
    { id: "play", group: "Lecture", label: s.playing ? "Pause" : "Lecture", keys: ["Espace"], icon: s.playing ? "pause" : "play", run: () => s.setPlaying(!s.playing) },
    { id: "home", group: "Lecture", label: "Aller au début", keys: ["Début"], icon: "skip-back", run: () => s.setPlayhead(0) },
    { id: "end", group: "Lecture", label: "Aller à la fin", keys: ["Fin"], icon: "skip-forward", run: () => s.setPlayhead(Number.MAX_SAFE_INTEGER) },
    { id: "prev", group: "Lecture", label: "Plan précédent", keys: ["↑"], icon: "chevron-up", run: () => s.jumpClip(-1), enabled: hasClips },
    { id: "next", group: "Lecture", label: "Plan suivant", keys: ["↓"], icon: "chevron-down", run: () => s.jumpClip(1), enabled: hasClips },
    { id: "split", group: "Montage", label: "Couper le plan au curseur", keys: ["S"], icon: "scissors", run: () => s.splitAtPlayhead(), enabled: hasClips },
    { id: "trim-in", group: "Montage", label: "Rogner l'entrée du plan au curseur", keys: ["I"], run: () => { const c = clip(); if (c) s.trimToPlayhead(c, "in"); }, enabled: () => !!clip() },
    { id: "trim-out", group: "Montage", label: "Rogner la sortie du plan au curseur", keys: ["O"], run: () => { const c = clip(); if (c) s.trimToPlayhead(c, "out"); }, enabled: () => !!clip() },
    { id: "dup", group: "Montage", label: "Dupliquer le ou les plans sélectionnés", keys: ["Ctrl", "D"], icon: "copy", run: () => { if (clip()) s.duplicateSelection(); }, enabled: () => !!clip() },
    { id: "select-all", group: "Montage", label: "Sélectionner tous les plans", keys: ["Ctrl", "A"], alt: "sélection multiple tout", run: () => s.selectAllClips(), enabled: hasClips },
    { id: "copy", group: "Montage", label: "Copier la sélection", keys: ["Ctrl", "C"], icon: "copy", alt: "plan élément presse-papiers", run: () => { s.copySelection(); }, enabled: () => !!s.item },
    { id: "cut", group: "Montage", label: "Couper la sélection (la mettre de côté)", keys: ["Ctrl", "X"], icon: "scissors", alt: "plan élément presse-papiers", run: () => s.cutSelection(), enabled: () => !!s.item },
    { id: "paste", group: "Montage", label: "Coller au curseur", keys: ["Ctrl", "V"], icon: "plus", alt: "insérer plan élément presse-papiers", run: () => s.paste(), enabled: () => !!s.clipboard },
    { id: "source-first", group: "Montage", label: "Ouvrir le moniteur source du premier rush", icon: "film", alt: "chutier rush insérer in out entrée sortie marquer", run: () => { const m = s.project?.media[0]; if (m) s.openSource(m.id); }, enabled: () => !!s.project?.media.length },
    { id: "del", group: "Montage", label: "Supprimer la sélection", keys: ["Suppr"], icon: "trash", alt: "retirer effacer",
      run: () => s.deleteSelection(), enabled: () => !!s.item },
    { id: "undo", group: "Montage", label: "Annuler", keys: ["Ctrl", "Z"], icon: "undo", run: () => s.undo(), enabled: () => s.past.length > 0 },
    { id: "redo", group: "Montage", label: "Rétablir", keys: ["Ctrl", "Y"], icon: "redo", run: () => s.redo(), enabled: () => s.future.length > 0 },
    { id: "text", group: "Ajouter", label: "Texte au curseur", icon: "plus", alt: "titre bandeau", run: () => s.addText() },
    { id: "sfx", group: "Ajouter", label: "Bruitage (whoosh) au curseur", icon: "volume", alt: "son effet", run: () => s.addSfx("whoosh") },
    { id: "marker", group: "Ajouter", label: "Repère au curseur (chapitre)", keys: ["M"], icon: "dot", alt: "chapitre youtube", run: () => s.addMarker(), enabled: hasClips },
    { id: "range-in", group: "Zone", label: "Entrée de la zone au curseur", keys: ["I"], alt: "in point début", run: () => s.markRange("in"), enabled: hasClips },
    { id: "range-out", group: "Zone", label: "Sortie de la zone au curseur", keys: ["O"], alt: "out point fin", run: () => s.markRange("out"), enabled: hasClips },
    { id: "range-clip", group: "Zone", label: "Zone sur le plan au curseur", keys: ["X"], icon: "square", run: () => s.rangeFromClip(), enabled: hasClips },
    { id: "range-loop", group: "Zone", label: s.loopRange ? "Arrêter la boucle sur la zone" : "Lire la zone en boucle", keys: ["Ctrl", "L"], icon: "redo", alt: "répéter", run: () => s.setLoopRange(!s.loopRange), enabled: () => !!s.timeline?.range },
    { id: "range-delete", group: "Zone", label: "Supprimer le contenu de la zone (ripple)", keys: ["Maj", "Suppr"], icon: "trash", alt: "retirer couper", run: () => s.deleteRange(), enabled: () => !!s.timeline?.range },
    { id: "range-export", group: "Zone", label: "Exporter seulement la zone…", icon: "download", run: () => s.setExportOpen(true), enabled: () => !!s.timeline?.range },
    { id: "range-clear", group: "Zone", label: "Effacer la zone", keys: ["Maj", "X"], icon: "x", run: () => s.setRange(null), enabled: () => !!s.timeline?.range },
    { id: "fmt-16-9", group: "Format", label: "Format 16:9 paysage", alt: "youtube horizontal", run: () => s.setTimelineProps({ format: "16:9" }) },
    { id: "fmt-9-16", group: "Format", label: "Format 9:16 vertical", alt: "short tiktok reels", run: () => s.setTimelineProps({ format: "9:16" }) },
    { id: "fmt-1-1", group: "Format", label: "Format 1:1 carré", alt: "instagram", run: () => s.setTimelineProps({ format: "1:1" }) },
    { id: "cap-karaoke", group: "Format", label: "Sous-titres animés mot par mot", alt: "karaoke", run: () => s.setTimelineProps({ captions: "karaoke" }) },
    { id: "cap-classic", group: "Format", label: "Sous-titres classiques", run: () => s.setTimelineProps({ captions: "classique" }) },
    { id: "cap-none", group: "Format", label: "Sans sous-titres", alt: "aucun", run: () => s.setTimelineProps({ captions: "aucun" }) },
    { id: "tab-rushes", group: "Ouvrir", label: "Rushes", icon: "film", alt: "import vidéos médias", run: () => s.setLeftTab("rushes") },
    { id: "tab-ia", group: "Ouvrir", label: "Montage automatique", icon: "wand", alt: "monter style durée ia", run: () => s.setLeftTab("ia") },
    { id: "tab-versions", group: "Ouvrir", label: "Versions et shorts", icon: "branch", run: () => s.setLeftTab("versions") },
    { id: "tab-modeles", group: "Ouvrir", label: "Modèles de style", icon: "grid", run: () => s.setLeftTab("modeles") },
    { id: "tab-marque", group: "Ouvrir", label: "Identité de marque", icon: "palette", alt: "logo couleurs", run: () => s.setLeftTab("marque") },
    { id: "tab-consigne", group: "Ouvrir", label: "Consigne et commentaires", keys: ["1"], icon: "message", run: () => s.setRightTab("consigne") },
    { id: "tab-montage", group: "Ouvrir", label: "Montage (format, sous-titres, rôles)", keys: ["2"], icon: "film", run: () => s.setRightTab("montage") },
    { id: "tab-plan", group: "Ouvrir", label: "Plan sélectionné", keys: ["3"], icon: "square", alt: "inspecteur élément", run: () => s.setRightTab("plan") },
    { id: "tab-habillage", group: "Ouvrir", label: "Habillage (look, LUT, textes, images)", keys: ["4"], icon: "sparkles", run: () => s.setRightTab("habillage") },
    { id: "tab-audio", group: "Ouvrir", label: "Audio et voix (musique, doublage, voix off)", keys: ["5"], icon: "volume", run: () => s.setRightTab("audio") },
    { id: "tab-qualite", group: "Ouvrir", label: "Qualité (contrôle, relecture)", keys: ["6"], icon: "check", run: () => s.setRightTab("qualite") },
    { id: "tab-texte", group: "Ouvrir", label: "Texte de la transcription", alt: "mots paroles", run: () => s.setRightTab("texte") },
    { id: "tab-recherche", group: "Ouvrir", label: "Recherche web", icon: "globe", alt: "chercher info internet faits", run: () => { s.setRightTab("recherche"); if (!s.layout.rightOpen) s.setLayout({ rightOpen: true }); } },
    { id: "export", group: "Ouvrir", label: "Exporter…", icon: "download", alt: "rendu télécharger mp4", run: () => s.setExportOpen(true), enabled: hasClips },
    { id: "settings", group: "Ouvrir", label: "Réglages", icon: "settings", alt: "clé claude whisper", run: () => { location.hash = "#/reglages"; } },
    { id: "projects", group: "Ouvrir", label: "Retour aux projets", icon: "back", alt: "accueil",
      run: () => { s.saveNow().catch(() => undefined).finally(() => { location.hash = ""; }); } },
    { id: "zoom-fit", group: "Affichage", label: "Zoom ajusté : tout le montage", keys: ["Maj", "Z"], run: () => s.zoomFit() },
    { id: "zoom-in", group: "Affichage", label: "Zoomer la timeline", keys: ["+"], run: () => s.setZoom(s.pxPerSec * 1.25) },
    { id: "zoom-out", group: "Affichage", label: "Dézoomer la timeline", keys: ["-"], run: () => s.setZoom(s.pxPerSec / 1.25) },
    { id: "help", group: "Affichage", label: "Raccourcis clavier", keys: ["?"], run: () => s.setHelp(true) },
    { id: "panel-left", group: "Affichage", label: s.layout.leftOpen ? "Replier le panneau de gauche" : "Afficher le panneau de gauche", icon: "panel-left", alt: "panneau masquer", run: () => s.setLayout({ leftOpen: !s.layout.leftOpen }) },
    { id: "panel-right", group: "Affichage", label: s.layout.rightOpen ? "Replier le panneau de droite" : "Afficher le panneau de droite", icon: "panel-right", alt: "panneau masquer inspecteur", run: () => s.setLayout({ rightOpen: !s.layout.rightOpen }) },
    { id: "layout-reset", group: "Affichage", label: "Espace de travail par défaut", icon: "layout", alt: "panneaux taille réinitialiser", run: () => s.resetLayout() },
  ];
}

export function CommandPalette({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const project = useStore((s) => s.project);
  const caps = useStore((s) => s.caps);
  useEffect(() => { input.current?.focus(); }, []);

  const all = useMemo(commands, []);
  const nq = norm(q.trim());
  const matches = useMemo(() => {
    const ok = all.filter((c) => !c.enabled || c.enabled());
    if (!nq) return ok;
    const words = nq.split(/\s+/);
    return ok.filter((c) => { const hay = norm(`${c.group} ${c.label} ${c.alt ?? ""}`); return words.every((w) => hay.includes(w)); });
  }, [all, nq]);
  // Sans commande qui corresponde, le texte devient une consigne pour l'IA (ou une consigne simple sans clé Claude).
  const askAi = q.trim().length > 2 && !!project && project.timeline.clips.length > 0;
  type Row = { kind: "group"; label: string } | { kind: "cmd"; c: Command; i: number } | { kind: "ai"; i: number };
  const flat: Row[] = [];
  let lastGroup = "";
  let n = 0;
  for (const c of matches) {
    if (c.group !== lastGroup) { flat.push({ kind: "group", label: c.group }); lastGroup = c.group; }
    flat.push({ kind: "cmd", c, i: n++ });
  }
  if (askAi) flat.push({ kind: "ai", i: n++ });
  const rows = flat.filter((r): r is Exclude<Row, { kind: "group" }> => r.kind !== "group");
  useEffect(() => { setCursor(0); }, [nq]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-i="${cursor}"]`)?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const runAi = (text: string) => {
    const st = useStore.getState();
    const pid = st.project?.id;
    if (!pid) return;
    const before = st.timeline;
    st.setRightTab("consigne");
    st.runJob("Consigne", () => api.command(pid, text), {
      timeline: true,
      onDone: (done) => {
        if (before) useStore.setState((x) => ({ past: [...x.past, before], future: [] }));
        const r = done.result as { message?: string } | null;
        useStore.getState().notify(r?.message ? `Consigne : ${r.message}` : "Consigne appliquée.");
      },
    });
  };
  const pick = (i: number) => {
    const row = rows[i];
    if (!row) return;
    onClose();
    if (row.kind === "ai") runAi(q.trim());
    else row.c.run();
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(rows.length - 1, c + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); pick(cursor); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };

  return (
    <div className="modal-backdrop palette-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal palette" role="dialog" aria-modal="true" aria-label="Palette de commandes">
        <div className="palette-input">
          <Icon name="search" size={18} />
          <input ref={input} value={q} placeholder="Une action, ou une consigne : « fais une version de 30 secondes »" aria-label="Commande"
            onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} spellCheck={false} />
          <Kbd k="Échap" />
        </div>
        <ul className="palette-list" ref={list} role="listbox">
          {flat.map((row) => row.kind === "group" ? (
            <li key={"g" + row.label} className="palette-group">{row.label}</li>
          ) : row.kind === "ai" ? (
            <li key="ai" data-i={row.i} role="option" aria-selected={cursor === row.i} className={`palette-row ai ${cursor === row.i ? "on" : ""}`}
              onMouseEnter={() => setCursor(row.i)} onMouseDown={(e) => { e.preventDefault(); pick(row.i); }}>
              <span className="palette-icon"><Icon name="sparkles" size={16} /></span>
              <span className="palette-label">Consigne : « {q.trim()} »</span>
              <span className="palette-hint">{caps && !caps.claude ? "Moteur local : consignes simples" : "Claude"}</span>
            </li>
          ) : (
            <li key={row.c.id} data-i={row.i} role="option" aria-selected={cursor === row.i} className={`palette-row ${cursor === row.i ? "on" : ""}`}
              onMouseEnter={() => setCursor(row.i)} onMouseDown={(e) => { e.preventDefault(); pick(row.i); }}>
              <span className="palette-icon">{row.c.icon ? <Icon name={row.c.icon} size={16} /> : null}</span>
              <span className="palette-label">{row.c.label}</span>
              {row.c.keys && <span className="palette-keys">{row.c.keys.map((k, j) => <Kbd key={j} k={k} />)}</span>}
            </li>
          ))}
          {!rows.length && <li className="palette-empty">Aucune commande. Importe une vidéo et monte-la pour donner des consignes.</li>}
        </ul>
      </div>
    </div>
  );
}
