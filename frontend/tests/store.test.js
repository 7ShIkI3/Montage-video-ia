// Sélection multiple : les actions du store, sans interface.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { spreadClipPatch, useStore } from "../src/store";

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
