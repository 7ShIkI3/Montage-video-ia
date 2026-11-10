// Module Texte : galerie de titres à poser au curseur, style et langue des sous-titres.
import { useState } from "react";
import { Icon } from "../../icons";
import { TextStyle, Timeline } from "../../api";
import { useStore } from "../../store";
import { CaptionLanguage } from "../AudioTools";
import { EmptyHint } from "../ui/EmptyHint";
import { ModuleFrame } from "./ModuleFrame";

type Sub = "titres" | "soustitres";

export function TextModule() {
  const tl = useStore((s) => s.timeline);
  const catalog = useStore((s) => s.catalog);
  const [sub, setSub] = useState<Sub>("titres");
  const { addText, setTimelineProps } = useStore.getState();
  return (
    <ModuleFrame module="texte" active={sub} onSelect={(id) => setSub(id as Sub)}>
      {sub === "titres" && (
        !catalog ? <EmptyHint icon="type" text="Chargement du catalogue…" /> : (
          <div className="lw-stack">
            <p className="muted small">Un clic pose le titre au curseur (2,5 s). Tu modifies ensuite son texte, sa durée et sa position dans l'onglet Plan.</p>
            <div className="lw-cards">
              {catalog.text_styles.map((o) => (
                <button key={o.id} type="button" className="lw-card text" data-style={o.id} onClick={() => addText({ style: o.id as TextStyle })}
                  title={`Poser un texte « ${o.label} » au curseur`}>
                  <span className="tc-sample" aria-hidden="true">Aa</span>
                  <span className="tc-name">{o.label}</span>
                </button>
              ))}
            </div>
          </div>
        )
      )}
      {sub === "soustitres" && (
        !tl ? <EmptyHint icon="type" text="Le montage se charge…" /> : (
          <div className="lw-stack">
            <label>Sous-titres
              <select value={tl.captions} onChange={(e) => setTimelineProps({ captions: e.target.value as Timeline["captions"] })}>
                <option value="karaoke">Animés</option><option value="classique">Classiques</option><option value="aucun">Aucun</option>
              </select>
            </label>
            <CaptionLanguage />
            <p className="muted small"><Icon name="type" size={12} /> Les sous-titres suivent la voix mot par mot. Le format du film se règle dans l'onglet Film.</p>
          </div>
        )
      )}
    </ModuleFrame>
  );
}
