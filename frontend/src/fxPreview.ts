// Approximation en direct (CSS) des effets rendus par FFmpeg à l'export.
import type { ColorAdjust, MixGroup, Mixer } from "./api";

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

/** Correction colorimétrique d'un plan en CSS, avec les mêmes coefficients que le rendu (render/effects.py,
 * color_filter) : luminosité +0,2 par unité d'exposition (≈ ×(1+0,4 e) au gris moyen), contraste 1+0,6 c,
 * saturation 1+s. La température est approchée : sépia + légère rotation de teinte, comme « chaud » / « froid ». */
export function colorCss(c?: ColorAdjust | null): string {
  if (!c) return "";
  const f: string[] = [];
  if (c.exposure) f.push(`brightness(${(1 + 0.4 * c.exposure).toFixed(3)})`);
  if (c.contrast) f.push(`contrast(${(1 + 0.6 * c.contrast).toFixed(3)})`);
  if (c.saturation) f.push(`saturate(${(1 + c.saturation).toFixed(3)})`);
  if (c.temperature > 0) f.push(`sepia(${(0.3 * c.temperature).toFixed(3)}) saturate(${(1 + 0.15 * c.temperature).toFixed(3)}) hue-rotate(${(-6 * c.temperature).toFixed(1)}deg)`);
  else if (c.temperature < 0) f.push(`hue-rotate(${(12 * c.temperature).toFixed(1)}deg) saturate(${(1 - 0.05 * c.temperature).toFixed(3)})`);
  return f.join(" ");
}

/** Le plan a-t-il une correction non nulle ? */
export function hasColor(c?: ColorAdjust | null): boolean {
  return !!c && !!(c.exposure || c.contrast || c.saturation || c.temperature);
}

export function cssFilter(effects: string[], look: string, color?: ColorAdjust | null): string {
  // La correction du plan passe avant les effets, comme au rendu.
  return [colorCss(color), ...[look, ...effects].map((e) => FILTERS[e])].filter(Boolean).join(" ") || "none";
}

/** Gain d'un groupe de sons du mixeur (0 si coupé). */
export function mixGain(mix: Mixer | undefined, group: MixGroup): number {
  const g = mix?.[group];
  return g ? (g.mute ? 0 : g.volume) : 1;
}

/** Classes d'animation et calques à afficher pour un plan. */
export function fxClasses(effects: string[], look: string): string[] {
  const all = new Set([look, ...effects]);
  const out: string[] = [];
  for (const k of ["glitch", "tremblement", "pulsation", "zoom_lent", "miroir", "3d_orbite", "3d_flottant"]) if (all.has(k)) out.push(`fx-${k}`);
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
