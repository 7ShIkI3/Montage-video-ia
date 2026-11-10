// Module Effets : effets vidéo et transitions du plan sélectionné, impacts 3D, bruitages et images à poser au curseur.
import { useState } from "react";
import { api, Opt } from "../../api";
import { Icon } from "../../icons";
import { CUTOUTS, useStore } from "../../store";
import { CutoutTools } from "../Lot3";
import { ImageButtons } from "../Studio";
import { EmptyHint } from "../ui/EmptyHint";
import { ModuleFrame } from "./ModuleFrame";

type Sub = "effets" | "transitions" | "impacts" | "bruitages" | "images";

const GROUP_LABELS: Record<string, string> = {
  couleur: "Couleur", style: "Style", mouvement: "Mouvement", "3d": "3D", ia: "Détourage (sans fond vert)",
};

const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

function NeedClip() {
  return <p className="lw-banner" role="status"><Icon name="crosshair" size={14} />Sélectionne un plan pour lui appliquer un effet</p>;
}

export function EffectsModule() {
  const tl = useStore((s) => s.timeline);
  const selected = useStore((s) => s.selected);
  const catalog = useStore((s) => s.catalog);
  const [sub, setSub] = useState<Sub>("effets");
  const { updateClip, addImpact, addSfx } = useStore.getState();
  const c = tl?.clips.find((x) => x.id === selected) ?? null;

  let body;
  if (!catalog) {
    body = <EmptyHint icon="zap" text="Chargement du catalogue…" />;
  } else if (sub === "effets") {
    const groups = Array.from(new Set(catalog.video_effects.map((o) => o.group || "Effets")));
    body = (
      <div className="lw-stack">
        {!c && <NeedClip />}
        {groups.map((g) => (
          <div key={g} className="lw-group">
            <div className="fx-title">{GROUP_LABELS[g] ?? g}</div>
            <div className="fx-chips">
              {catalog.video_effects.filter((o: Opt) => (o.group || "Effets") === g).map((o) => (
                <button key={o.id} type="button" className={`fx-chip ${c?.effects.includes(o.id) ? "on" : ""}`} disabled={!c}
                  aria-pressed={!!c?.effects.includes(o.id)}
                  onClick={() => c && updateClip(c.id, {
                    // Un seul fond de détourage à la fois sur un plan.
                    effects: CUTOUTS.includes(o.id) && !c.effects.includes(o.id)
                      ? [...c.effects.filter((e) => !CUTOUTS.includes(e)), o.id] : toggle(c.effects, o.id),
                  })}>{o.label}</button>
              ))}
            </div>
            {g === "ia" && c && <CutoutTools c={c} />}
          </div>
        ))}
      </div>
    );
  } else if (sub === "transitions") {
    body = (
      <div className="lw-stack">
        {!c && <NeedClip />}
        <p className="muted small">La transition s'applique à l'entrée du plan sélectionné. Sa durée se règle dans l'onglet Plan.</p>
        <div className="lw-cards">
          {catalog.transitions.map((o) => (
            <button key={o.id} type="button" className={`lw-card ${c?.transition === o.id ? "on" : ""}`} disabled={!c} aria-pressed={c?.transition === o.id}
              onClick={() => c && updateClip(c.id, { transition: o.id })}>
              <Icon name="transition" size={16} /><span className="tc-name">{o.label}</span>
            </button>
          ))}
        </div>
      </div>
    );
  } else if (sub === "impacts") {
    body = (
      <div className="lw-stack">
        <p className="muted small">Un impact 3D (secousse, flash, onde…) se pose au curseur de lecture.</p>
        <div className="lw-cards">
          {(catalog.impacts ?? []).map((o) => (
            <button key={o.id} type="button" className="lw-card" onClick={() => addImpact(o.id)} title={`Poser « ${o.label} » au curseur`}>
              <Icon name="zap" size={16} /><span className="tc-name">{o.label}</span>
            </button>
          ))}
        </div>
      </div>
    );
  } else if (sub === "bruitages") {
    body = (
      <div className="lw-stack">
        <p className="muted small">Un bruitage se pose au curseur de lecture. Le bouton lecture l'écoute avant de l'ajouter.</p>
        <ul className="lw-sfx">
          {catalog.sfx.map((o) => (
            <li key={o.id}>
              <button type="button" className="icon-btn" aria-label={`Écouter ${o.label}`} title="Écouter"
                onClick={() => new Audio(api.sfxUrl(o.id)).play().catch(() => undefined)}><Icon name="play" size={12} /></button>
              <span className="lw-sfx-name">{o.label}</span>
              <button type="button" className="small-btn" onClick={() => addSfx(o.id)}><Icon name="plus" size={13} strokeWidth={2.4} />Ajouter</button>
            </li>
          ))}
        </ul>
      </div>
    );
  } else {
    body = (
      <div className="lw-stack">
        <p className="muted small">Un logo ou un filigrane reste affiché sur tout le montage ; une image au curseur apparaît quelques secondes.</p>
        <ImageButtons />
      </div>
    );
  }

  return (
    <ModuleFrame module="effets" active={sub} onSelect={(id) => setSub(id as Sub)}>
      {body}
    </ModuleFrame>
  );
}
