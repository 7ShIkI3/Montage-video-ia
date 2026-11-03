// Approximation en direct (CSS) des effets rendus par FFmpeg à l'export.
const FILTERS: Record<string, string> = {
  noir_et_blanc: "grayscale(1) contrast(1.1)",
  sepia: "sepia(0.85)",
  vintage: "sepia(0.35) saturate(0.8) contrast(0.92) brightness(1.04)",
  vif: "saturate(1.35) contrast(1.08)",
  cinema: "contrast(1.1) saturate(0.88) brightness(0.97)",
  froid: "hue-rotate(-12deg) saturate(1.05) brightness(1.02)",
  chaud: "sepia(0.18) saturate(1.2) hue-rotate(-6deg)",
  lumineux: "brightness(1.18)",
  contraste: "contrast(1.3)",
  nettete: "contrast(1.05)",
  flou: "blur(4px)",
};

export function cssFilter(effects: string[], look: string): string {
  return [look, ...effects].map((e) => FILTERS[e]).filter(Boolean).join(" ") || "none";
}

/** Classes d'animation et calques à afficher pour un plan. */
export function fxClasses(effects: string[], look: string): string[] {
  const all = new Set([look, ...effects]);
  const out: string[] = [];
  for (const k of ["glitch", "tremblement", "pulsation", "zoom_lent", "miroir"]) if (all.has(k)) out.push(`fx-${k}`);
  return out;
}

export function overlaysFor(effects: string[], look: string) {
  const all = new Set([look, ...effects]);
  return {
    letterbox: all.has("cinema"),
    vignette: all.has("vignette") || all.has("vintage") || all.has("cinema"),
    grain: all.has("grain") || all.has("vintage"),
  };
}
