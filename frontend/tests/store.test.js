// Sélection multiple, disposition, navigation : les actions du store, sans interface.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, test } from "node:test";
import { clampLayout, DEFAULT_LAYOUT, defaultLayout, editStep, LAYOUT_LIMITS, loadLayout, RIGHT_TAB_ORDER, spreadClipPatch, useStore } from "../src/store";
import { LEFT_MODULES, leftModuleOf, leftTabLabel, RIGHT_TABS, rightTabHint, rightTabTitle } from "../src/labels";

const clip = (id, extra = {}) => ({
  id, media_id: "m1", src_in: 0, src_out: 2, segment_id: null, score: 0, reason: "", zoom: 1, volume: 1, fade_in: 0, fade_out: 0,
  speed: 1, transition: "aucune", transition_duration: 0.35, effects: [], audio_effects: [], focus_x: null, focus_y: null,
  masks: [], keyframes: [], ...extra,
});
const order = () => useStore.getState().timeline.clips.map((c) => c.id).join("");
const sel = () => [...useStore.getState().selectedIds].sort().join("");
const byId = (id) => useStore.getState().timeline.clips.find((c) => c.id === id);
const S = () => useStore.getState();

beforeEach(() => {
  useStore.setState({
    project: { id: "p1", media: [{ id: "m1", duration: 60, has_video: true }] },
    timeline: { clips: ["a", "b", "c", "d", "e"].map((id) => clip(id)), rejected: [], texts: [], sfx: [], overlays: [] },
    past: [], future: [], selected: null, item: null, selectedIds: [], playhead: 0, clipboard: null,
  });
});

test("Ctrl + clic ajoute puis retire ; le plan principal suit", () => {
  S().select("a"); S().toggleSelect("c");
  assert.equal(sel(), "ac"); assert.equal(S().selected, "c");
  S().toggleSelect("c");
  assert.equal(sel(), "a"); assert.equal(S().selected, "a");
  S().toggleSelect("a");
  assert.equal(sel(), ""); assert.equal(S().item, null);
});

test("Maj + clic sélectionne l'intervalle, dans les deux sens, et garde l'ancre", () => {
  S().select("d"); S().selectRange("b");
  assert.equal(sel(), "bcd"); assert.equal(S().selected, "d");
  S().selectRange("e");
  assert.equal(sel(), "de");
  S().selectItem(null); S().selectRange("c");
  assert.equal(sel(), "c", "sans ancre : le plan cliqué seul");
});

test("un clic simple, un autre élément ou Échap réduisent la sélection", () => {
  S().selectAllClips(); assert.equal(sel(), "abcde");
  S().select("b"); assert.equal(sel(), "b");
  S().selectAllClips(); S().selectItem({ kind: "text", id: "t1" }); assert.equal(sel(), "");
  S().selectAllClips(); S().selectItem(null); assert.equal(sel(), "");
});

test("supprimer le groupe : une seule étape d'annulation, plans mis de côté", () => {
  S().select("b"); S().toggleSelect("d"); S().deleteSelection();
  assert.equal(order(), "ace"); assert.equal(S().timeline.rejected.length, 2); assert.equal(sel(), "");
  S().undo();
  assert.equal(order(), "abcde"); assert.equal(S().past.length, 0);
});

test("déplacer un groupe : regroupé, dans l'ordre du montage, sans étape inutile", () => {
  S().moveClips(["d", "b"], 0); assert.equal(order(), "bdace");
  S().moveClips(["b", "d"], 5); assert.equal(order(), "acebd");
  const steps = S().past.length;
  S().moveClips(["b", "d"], 5); assert.equal(S().past.length, steps, "déjà en place : rien à annuler");
  S().moveClips(["zzz"], 2); assert.equal(order(), "acebd");
});

test("décaler le groupe d'un cran, bords compris", () => {
  S().select("b"); S().toggleSelect("c");
  S().nudgeSelection(1); assert.equal(order(), "adbce");
  S().nudgeSelection(1); assert.equal(order(), "adebc");
  S().nudgeSelection(1); assert.equal(order(), "adebc", "déjà au bout");
  S().nudgeSelection(-1); assert.equal(order(), "adbce");
  assert.equal(sel(), "bc");
});

test("copier-coller et dupliquer plusieurs plans", () => {
  S().select("a"); S().toggleSelect("c");
  assert.equal(S().copySelection(), true);
  useStore.setState({ playhead: 10 });  // fin du montage (5 plans de 2 s)
  S().paste();
  const ids = S().timeline.clips.map((c) => c.id);
  assert.equal(ids.length, 7); assert.equal(S().selectedIds.length, 2);
  assert.deepEqual(S().selectedIds, ids.slice(5), "les copies sont à la fin et sélectionnées");
  S().undo(); S().select("b"); S().toggleSelect("c"); S().duplicateSelection();
  assert.equal(S().timeline.clips.length, 7);
  assert.deepEqual(S().timeline.clips.slice(3, 5).map((c) => c.id), S().selectedIds, "les copies suivent le dernier plan du groupe");
});

test("couper un groupe ne le met pas dans les plans écartés", () => {
  S().select("a"); S().toggleSelect("e"); S().cutSelection();
  assert.equal(order(), "bcd"); assert.equal(S().timeline.rejected.length, 0); assert.equal(S().clipboard.clips.length, 2);
});

test("un réglage simple s'applique à tout le groupe, le rognage non", () => {
  S().select("a"); S().toggleSelect("b");
  S().updateClip("b", { volume: 0.4, src_out: 1.5 });
  assert.equal(byId("a").volume, 0.4); assert.equal(byId("a").src_out, 2); assert.equal(byId("c").volume, 1);
});

test("cocher un effet ne remplace pas les effets propres des autres plans", () => {
  S().updateClip("b", { effects: ["flou"], audio_effects: ["echo"] });
  S().select("b"); S().toggleSelect("a");  // a est le plan principal, sans effet
  S().updateClip("a", { effects: ["noir_blanc"] });
  assert.deepEqual(byId("b").effects, ["flou", "noir_blanc"]);
  S().updateClip("a", { audio_effects: ["muet"] });
  assert.deepEqual(byId("b").audio_effects, ["echo", "muet"]);
  S().updateClip("a", { effects: [] });
  assert.deepEqual(byId("b").effects, ["flou"], "décocher retire seulement cet effet");
});

test("un seul fond de détourage par plan, même reporté", () => {
  S().updateClip("b", { effects: ["detourage_noir", "flou"] });
  S().select("b"); S().toggleSelect("a");
  S().updateClip("a", { effects: ["detourage_flou"] });
  assert.deepEqual(byId("b").effects, ["flou", "detourage_flou"]);
});

test("couleur : seul le curseur déplacé suit", () => {
  S().updateClip("b", { color: { exposure: 0.3, contrast: 0, saturation: 0, temperature: -0.2 } });
  S().select("b"); S().toggleSelect("a");
  S().updateClip("a", { color: { exposure: 0, contrast: 0, saturation: 0.5, temperature: 0 } });
  assert.deepEqual(byId("b").color, { exposure: 0.3, contrast: 0, saturation: 0.5, temperature: -0.2 });
  S().updateClip("a", { color: null });
  assert.equal(byId("b").color, null, "réinitialiser vaut pour tous");
});

test("spreadClipPatch ignore les réglages propres à un plan", () => {
  const before = clip("x"), after = clip("x", { src_in: 1, keyframes: [{ t: 0, zoom: 2 }] }), other = clip("y");
  spreadClipPatch(before, after, { src_in: 1, keyframes: after.keyframes }, other);
  assert.equal(other.src_in, 0); assert.deepEqual(other.keyframes, []);
});

test("la sélection oublie les plans disparus", () => {
  S().selectAllClips();
  S().edit((tl) => { tl.clips = tl.clips.filter((c) => c.id !== "e" && c.id !== "d"); });
  assert.equal(sel(), "abc");
});

// ---------------------------------------------------------------------------------------------
// Refonte « Studio » : disposition, étape du parcours, navigation entre panneaux
// ---------------------------------------------------------------------------------------------

/** localStorage simulé (Node n'en a pas) ; renvoie la table pour vérifier ce qui est écrit. */
function mockStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  const ls = { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => void data.set(k, String(v)), removeItem: (k) => void data.delete(k) };
  Object.defineProperty(globalThis, "localStorage", { value: ls, configurable: true, writable: true });
  return data;
}
afterEach(() => {
  delete globalThis.localStorage;
  delete globalThis.requestAnimationFrame;
  delete globalThis.document;
});

test("disposition par défaut : grande fenêtre à partir de 1400 px, sinon serrée", () => {
  const big = { left: 400, right: 360, bottom: 300, leftOpen: true, rightOpen: true, thumbs: true };
  const small = { left: 340, right: 320, bottom: 250, leftOpen: true, rightOpen: true, thumbs: true };
  assert.deepEqual(defaultLayout(1440), big);
  assert.deepEqual(defaultLayout(1400), big);
  assert.deepEqual(defaultLayout(1399), small);
  assert.deepEqual(defaultLayout(1280), small);
  assert.deepEqual(defaultLayout(), big, "sans fenêtre (Node) : 1440 par défaut");
  assert.deepEqual(DEFAULT_LAYOUT, big);
  assert.notEqual(defaultLayout(1280), defaultLayout(1280), "un objet neuf à chaque appel");
});

test("bornes de la disposition : gauche 320-640, droite 280-640, bas 150 minimum", () => {
  assert.deepEqual(LAYOUT_LIMITS.left, [320, 640]);
  assert.deepEqual(LAYOUT_LIMITS.right, [280, 640]);
  assert.deepEqual(LAYOUT_LIMITS.bottom, [150, 0.7]);
  const c = clampLayout({ ...defaultLayout(1440), left: 100, right: 9999, bottom: 10 });
  assert.equal(c.left, 320); assert.equal(c.right, 640); assert.equal(c.bottom, 150);
});

test("disposition mémorisée : nouvelle clé v2, ancienne clé reprise pour les vignettes seulement", () => {
  // Rien de mémorisé, ou pas de localStorage du tout.
  assert.deepEqual(loadLayout(1280), defaultLayout(1280));
  mockStorage();
  assert.deepEqual(loadLayout(1280), defaultLayout(1280));
  // Ancienne disposition : ni largeurs, ni panneaux repliés ne passent, les vignettes oui.
  mockStorage({ "monteur.layout": JSON.stringify({ left: 240, right: 300, bottom: 200, leftOpen: false, thumbs: false }) });
  assert.deepEqual(loadLayout(1280), { ...defaultLayout(1280), thumbs: false });
  mockStorage({ "monteur.layout": JSON.stringify({ left: 240, thumbs: "oui" }) });
  assert.deepEqual(loadLayout(1440), defaultLayout(1440), "thumbs invalide : ignoré");
  // La v2 l'emporte, et se recale dans les bornes.
  mockStorage({ "monteur.layout": JSON.stringify({ thumbs: false }), "monteur.layout.v2": JSON.stringify({ left: 100, right: 5000, bottom: 260, leftOpen: false, thumbs: true }) });
  const l = loadLayout(1440);
  assert.equal(l.left, 320); assert.equal(l.right, 640); assert.equal(l.bottom, 260); assert.equal(l.leftOpen, false); assert.equal(l.thumbs, true);
  // Contenu illisible : disposition par défaut.
  mockStorage({ "monteur.layout.v2": "{pas du json" });
  assert.deepEqual(loadLayout(1280), defaultLayout(1280));
});

test("la première écriture en v2 supprime l'ancienne clé ; réinitialiser rend la disposition de la fenêtre", () => {
  const data = mockStorage({ "monteur.layout": JSON.stringify({ left: 240 }) });
  useStore.setState({ layout: defaultLayout(1440) });
  S().setLayout({ left: 500 });
  assert.equal(data.has("monteur.layout"), false);
  assert.equal(JSON.parse(data.get("monteur.layout.v2")).left, 500);
  S().resetLayout();
  assert.deepEqual(S().layout, defaultLayout());
  assert.equal(JSON.parse(data.get("monteur.layout.v2")).left, 400);
});

test("setLayout sans localStorage ne casse rien", () => {
  useStore.setState({ layout: defaultLayout(1440) });
  S().setLayout({ right: 300 });
  assert.equal(S().layout.right, 300);
});

test("editStep : importer, monter, exporter, sur la timeline en cours d'édition", () => {
  const media = [{ id: "m1", duration: 60, has_video: true }];
  const tl = (n) => ({ clips: Array.from({ length: n }, (_, i) => clip("c" + i)) });
  assert.equal(editStep({ project: null }), "importer");
  assert.equal(editStep({ project: { id: "p", media: [], timeline: tl(0) } }), "importer");
  assert.equal(editStep({ project: { id: "p", media, timeline: tl(0) } }), "monter");
  assert.equal(editStep({ project: { id: "p", media, timeline: tl(3) } }), "exporter");
  // Plans supprimés après montage : project.timeline garde les anciens plans, la timeline en cours est vide.
  assert.equal(editStep({ project: { id: "p", media, timeline: tl(3) }, timeline: tl(0) }), "monter");
  assert.equal(editStep({ project: { id: "p", media, timeline: tl(0) }, timeline: tl(2) }), "exporter");
  assert.equal(editStep({ project: { id: "p", media, timeline: tl(3) }, timeline: null }), "exporter", "timeline absente : celle du projet");
  assert.equal(editStep(S()), "exporter", "état de test : un projet avec média et cinq plans");
});

test("markBuilt note l'instant d'arrivée du montage", () => {
  useStore.setState({ builtAt: 0 });
  const t0 = Date.now();
  S().markBuilt();
  assert.ok(S().builtAt >= t0 && S().builtAt <= Date.now());
});

test("setAssistantView bascule entre Discuter et Commentaires", () => {
  useStore.setState({ assistantView: "chat" });
  S().setAssistantView("comments"); assert.equal(S().assistantView, "comments");
  S().setAssistantView("chat"); assert.equal(S().assistantView, "chat");
});

test("openLeft et openRight ouvrent l'onglet et déplient le panneau replié", () => {
  mockStorage();
  useStore.setState({ layout: { ...defaultLayout(1440), leftOpen: false, rightOpen: false }, leftTab: null, rightTab: "consigne" });
  S().openLeft("modeles");
  assert.equal(S().leftTab, "modeles"); assert.equal(S().layout.leftOpen, true); assert.equal(S().layout.rightOpen, false);
  S().openRight("export");
  assert.equal(S().rightTab, "export"); assert.equal(S().layout.rightOpen, true);
  const layout = S().layout;
  S().openLeft("effets"); S().openRight("plan");
  assert.equal(S().leftTab, "effets"); assert.equal(S().rightTab, "plan");
  assert.equal(S().layout, layout, "panneaux déjà ouverts : la disposition n'est pas réécrite");
});

test("focusComment : onglet Chat, vue Commentaires, panneau déplié, focus après deux images", () => {
  mockStorage();
  useStore.setState({ layout: { ...defaultLayout(1440), rightOpen: false }, rightTab: "export", assistantView: "chat" });
  const queue = [];
  let focused = 0;
  globalThis.requestAnimationFrame = (f) => void queue.push(f);
  globalThis.document = { getElementById: (id) => (id === "comment-box" ? { focus: () => { focused++; } } : null) };
  S().focusComment();
  assert.equal(S().rightTab, "consigne"); assert.equal(S().layout.rightOpen, true); assert.equal(S().assistantView, "comments");
  assert.equal(focused, 0, "pas de focus tant que le panneau n'a pas eu ses deux rendus");
  queue.shift()();
  assert.equal(focused, 0);
  queue.shift()();
  assert.equal(focused, 1); assert.equal(queue.length, 0);
});

test("focusComment sans navigateur (Node) ou sans zone de commentaire : aucune erreur", () => {
  useStore.setState({ layout: defaultLayout(1440), rightTab: "plan", assistantView: "chat" });
  S().focusComment();
  assert.equal(S().rightTab, "consigne"); assert.equal(S().assistantView, "comments");
  globalThis.requestAnimationFrame = (f) => f();
  globalThis.document = { getElementById: () => null };
  S().focusComment();
});

test("libellés : les neuf onglets droite suivent RIGHT_TAB_ORDER, les huit onglets gauche ont chacun un module", () => {
  assert.deepEqual(RIGHT_TABS.map((t) => t.id), RIGHT_TAB_ORDER);
  assert.deepEqual(RIGHT_TABS.map((t) => t.key), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(rightTabTitle("consigne"), "Réalisateur"); assert.equal(rightTabTitle("montage"), "Fiche du film");
  assert.equal(rightTabHint(RIGHT_TABS[0]), "Chat (1)"); assert.equal(rightTabHint(RIGHT_TABS[8]), "Web (9)");
  const all = ["rushes", "ia", "audio", "texte", "effets", "versions", "modeles", "marque"];
  assert.equal(LEFT_MODULES.length, 6);
  for (const t of all) assert.equal(LEFT_MODULES.filter((m) => m.tabs.includes(t)).length, 1, `${t} : un seul module`);
  assert.deepEqual(LEFT_MODULES.flatMap((m) => m.tabs).sort(), [...all].sort());
  for (const m of LEFT_MODULES) assert.ok(m.tabs.includes(m.tab), `${m.id} : l'onglet d'ouverture fait partie du module`);
  for (const t of ["versions", "modeles", "marque"]) assert.equal(leftModuleOf(t).id, "projet");
  assert.equal(leftModuleOf("rushes").label, "Médias");
  assert.deepEqual(leftModuleOf("marque").subs.map((x) => x.tab), ["versions", "modeles", "marque"]);
  assert.equal(leftTabLabel("ia"), "IA"); assert.equal(leftTabLabel("modeles"), "Projet : Modèles");
});

test("libellés : chaque icône citée existe dans icons.tsx", () => {
  const src = readFileSync(join(process.env.FRONTEND_DIR ?? process.cwd(), "src", "icons.tsx"), "utf8");
  const has = (n) => new RegExp(`^\\s*"?${n}"?:`, "m").test(src);
  for (const n of [...LEFT_MODULES.map((m) => m.icon), ...RIGHT_TABS.map((t) => t.icon)]) assert.ok(has(n), `icône « ${n} » absente de icons.tsx`);
  for (const n of ["type", "layers", "crosshair", "shield-check", "align-left", "more", "repeat", "flag", "range", "maximize", "chevron-left", "transition", "clapper"])
    assert.ok(has(n), `icône « ${n} » à créer`);
});
