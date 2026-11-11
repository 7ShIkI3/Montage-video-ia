// Mises à jour : la logique pure (src/update.ts) et les états du parcours dans le store, sans navigateur ni réseau
// (fetch est simulé). Rien n'est téléchargé ni installé sans un clic : les tests le vérifient aussi.
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { kindLabel, useStore } from "../src/store";
import {
  aboutStatus, announceable, compareVersions, effectivePhase, formatSize, friendlyError, installBlock, notesText, NOTES_MAX, parseVersion,
  readSkipped, safeHttpsUrl, sameVersion, shouldShow, SKIP_KEY, updateLabel, writeSkipped,
} from "../src/update";

const info = (extra = {}) => ({
  current: "1.1.0", latest: "1.2.0", available: true, notes: "Nouveautés", published_at: "2026-12-03T10:00:00Z", size: 1_255_508_793,
  page_url: "https://github.com/7ShIkI3/Montage-video-ia/releases/tag/v1.2.0", installable: true, downloaded: false,
  checked_at: "2026-12-04T09:30:00Z", enabled: true, error: null, ...extra,
});
const job = (extra = {}) => ({
  id: "j1", project_id: "_bibliotheque", kind: "mise_a_jour", status: "termine", progress: 1, message: "", error: null, result: null,
  created_at: 1, started_at: 1, updated_at: 1, finished_at: 2, stage: null, steps: [], cancel_requested: false, ...extra,
});
const S = () => useStore.getState();

// --- Comparaison de versions ---

test("les versions se comparent chiffre par chiffre, jamais en texte", () => {
  assert.equal(compareVersions("1.0.9", "1.0.10"), -1);
  assert.equal(compareVersions("1.0.10", "1.1.0"), -1);
  assert.equal(compareVersions("1.10.0", "1.9.9"), 1);
  assert.equal(compareVersions("2.0.0", "1.99.99"), 1);
  assert.equal(compareVersions("1.1.0", "1.1.0"), 0);
  assert.equal(compareVersions("1.1", "1.1.0"), 0, "un chiffre manquant vaut zéro");
});

test("le préfixe « v » et les suffixes de préversion ne gênent pas ; une version illisible ne compare à rien", () => {
  assert.deepEqual(parseVersion("v1.2.10"), [1, 2, 10]);
  assert.deepEqual(parseVersion("1.2.0-beta.1"), [1, 2, 0]);
  assert.deepEqual(parseVersion("1.2.0+build5"), [1, 2, 0]);
  assert.equal(parseVersion("latest"), null);
  assert.equal(parseVersion(""), null);
  assert.equal(parseVersion(null), null);
  assert.equal(compareVersions("v1.2.0", "1.1.0"), 1);
  assert.equal(compareVersions("latest", "1.1.0"), 0);
  assert.ok(sameVersion("v1.2.0", "1.2.0"));
  assert.ok(!sameVersion("1.2.0", "1.2.1"));
  assert.ok(!sameVersion("1.2.0", ""));
});

// --- Ignorer une version ---

test("une version ignorée n'est plus annoncée, la suivante l'est", () => {
  assert.equal(announceable(info(), ""), true);
  assert.equal(announceable(info(), "1.2.0"), false);
  assert.equal(announceable(info(), "v1.2.0"), false, "écriture avec « v » : même version");
  assert.equal(announceable(info({ latest: "1.3.0" }), "1.2.0"), true);
});

test("rien à annoncer sans version plus récente", () => {
  assert.equal(announceable(null, ""), false);
  assert.equal(announceable(info({ available: false }), ""), false);
  assert.equal(announceable(info({ latest: null }), ""), false);
  assert.equal(announceable(info({ latest: "1.0.0" }), ""), false, "le serveur dit « disponible » mais ce n'est pas plus récent");
});

test("la version ignorée est lue et écrite dans localStorage, sans planter s'il manque", () => {
  assert.equal(SKIP_KEY, "monteur.update.skip");
  assert.equal(readSkipped(), "", "pas de localStorage du tout");
  writeSkipped("1.2.0");   // ne doit pas lever
  const table = {};
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true, writable: true,
    value: { getItem: (k) => table[k] ?? null, setItem: (k, v) => { table[k] = String(v); }, removeItem: (k) => { delete table[k]; } },
  });
  try {
    writeSkipped("1.2.0");
    assert.equal(table[SKIP_KEY], "1.2.0");
    assert.equal(readSkipped(), "1.2.0");
    writeSkipped("");
    assert.equal(readSkipped(), "");
    assert.ok(!(SKIP_KEY in table));
  } finally {
    delete globalThis.localStorage;
  }
});

// --- Libellés et textes ---

test("la taille du téléchargement s'écrit en français", () => {
  assert.equal(formatSize(1_255_508_793), "1,3 Go");
  assert.equal(formatSize(85_400_000), "85 Mo");
  assert.equal(formatSize(40_000), "40 Ko");
  assert.equal(formatSize(null), "");
  assert.equal(formatSize(0), "");
});

test("les notes restent du texte : retours à la ligne unifiés, tronquées, jamais interprétées", () => {
  assert.equal(notesText("a\r\nb\rc"), "a\nb\nc");
  assert.equal(notesText("  <img src=x onerror=alert(1)>  "), "<img src=x onerror=alert(1)>", "le HTML reste du texte, c'est React qui l'échappera");
  assert.equal(notesText("x".repeat(NOTES_MAX + 500)).length, NOTES_MAX);
  assert.equal(notesText(null), "");
});

test("le lien de la page de la version n'est suivi qu'en https", () => {
  assert.equal(safeHttpsUrl("https://github.com/7ShIkI3/Montage-video-ia/releases/tag/v1.2.0"), "https://github.com/7ShIkI3/Montage-video-ia/releases/tag/v1.2.0");
  assert.equal(safeHttpsUrl("javascript:alert(1)"), null);
  assert.equal(safeHttpsUrl("http://github.com/x"), null);
  assert.equal(safeHttpsUrl("file:///C:/x.exe"), null);
  assert.equal(safeHttpsUrl("pas un lien"), null);
  assert.equal(safeHttpsUrl(null), null);
});

test("le libellé de la pastille suit le parcours", () => {
  assert.equal(updateLabel(info(), "idle", null), "Version 1.2.0 disponible");
  assert.equal(updateLabel(info(), "downloading", job({ progress: 0.42, status: "en_cours" })), "Téléchargement de la version 1.2.0 : 42 %");
  assert.equal(updateLabel(info({ downloaded: true }), "idle", null), "Version 1.2.0 prête à installer");
  assert.equal(updateLabel(info(), "installing", null), "Installation en cours…");
  assert.equal(updateLabel(null, "idle", null), "");
  assert.equal(kindLabel("mise_a_jour"), "Mise à jour");
});

test("la section À propos dit « dernière version » ou « disponible » ou l'erreur", () => {
  assert.deepEqual(aboutStatus(info({ available: false, latest: "1.1.0" }), false), { tone: "ok", text: "Tu as la dernière version." });
  assert.equal(aboutStatus(info(), false).tone, "update");
  assert.match(aboutStatus(info(), false).text, /1\.2\.0/);
  assert.deepEqual(aboutStatus(info({ error: "Pas de connexion à GitHub." }), false), { tone: "error", text: "Pas de connexion à GitHub." });
  assert.equal(aboutStatus(info(), true).tone, "busy");
  assert.equal(aboutStatus(null, false).tone, "idle");
  assert.equal(aboutStatus(info({ available: false, latest: null, checked_at: null }), false).tone, "idle", "jamais vérifié : on ne ment pas");
});

test("les erreurs s'affichent en clair, sans jargon", () => {
  assert.equal(friendlyError(new Error("Une tâche est en cours : attends sa fin.")), "Une tâche est en cours : attends sa fin.");
  assert.match(friendlyError(new TypeError("Failed to fetch")), /ne répond pas/);
  assert.match(friendlyError(new Error("500 Internal Server Error")), /problème \(code 500\)/);
  assert.match(friendlyError(undefined), /ne répond pas/);
});

// --- Peut-on installer ? ---

test("l'installation est refusée tant que ce n'est pas possible, et dit pourquoi", () => {
  assert.equal(installBlock(info({ downloaded: true }), false), null);
  assert.match(installBlock(info({ downloaded: true }), true), /export|tâche/);
  assert.match(installBlock(info({ installable: false, downloaded: true }), false), /ne s'installe pas/);
  assert.match(installBlock(info(), false), /Télécharge d'abord/);
  assert.match(installBlock(null, false), /Aucune mise à jour/);
  assert.match(installBlock(info({ available: false }), false), /Aucune mise à jour/);
});

test("une mise à jour déjà téléchargée est « prête » dès l'ouverture", () => {
  assert.equal(effectivePhase(info({ downloaded: true }), "idle"), "ready");
  assert.equal(effectivePhase(info(), "idle"), "idle");
  assert.equal(effectivePhase(info({ downloaded: true, installable: false }), "idle"), "idle");
  assert.equal(effectivePhase(info({ downloaded: true }), "installing"), "installing");
});

// --- Parcours dans le store (fetch simulé) ---

let calls;
/** Simule fetch : `routes` associe « MÉTHODE /chemin » à un corps (objet), ou à { status, detail } pour une erreur. */
function fakeFetch(routes) {
  globalThis.fetch = async (url, init = {}) => {
    const key = `${init.method ?? "GET"} ${url}`;
    calls.push(key);
    const r = routes[key] ?? routes[key.split("?")[0]];
    if (r === undefined) throw new TypeError("Failed to fetch");
    const err = r && typeof r === "object" && "status" in r && "detail" in r;
    const body = typeof r === "function" ? r() : r;
    return err
      ? { ok: false, status: body.status, statusText: "Conflict", json: async () => ({ detail: body.detail }) }
      : { ok: true, status: 200, statusText: "OK", json: async () => body };
  };
}
const fresh = () => useStore.setState({
  update: null, updateChecking: false, updateLaunchDone: false, updateSkipped: "", updateHidden: false, updateOpen: false,
  updatePhase: "idle", updateJob: null, updateError: null, jobs: {}, toasts: [], project: null, timeline: null, dirty: false,
});
beforeEach(() => { calls = []; fresh(); });
afterEach(() => { delete globalThis.fetch; delete globalThis.localStorage; });

test("au lancement, la vérification se fait une seule fois", async () => {
  fakeFetch({ "GET /api/settings": { check_updates: true }, "GET /api/update": info() });
  await S().launchUpdateCheck();
  await S().launchUpdateCheck();
  assert.equal(calls.filter((c) => c.startsWith("GET /api/update")).length, 1);
  assert.equal(S().update.latest, "1.2.0");
  assert.equal(announceable(S().update, S().updateSkipped), true);
});

test("au lancement, rien n'est demandé si le réglage est coupé", async () => {
  fakeFetch({ "GET /api/settings": { check_updates: false }, "GET /api/update": info() });
  await S().launchUpdateCheck();
  assert.equal(calls.filter((c) => c.startsWith("GET /api/update")).length, 0);
  assert.equal(S().update, null);
});

test("au lancement, une panne ne dit rien (pas de message rouge)", async () => {
  fakeFetch({});   // tout échoue
  await S().launchUpdateCheck();
  assert.equal(S().toasts.length, 0);
  assert.equal(S().update, null);
  assert.equal(S().updateChecking, false);
});

test("une vérification demandée à la main qui échoue le dit en clair", async () => {
  fakeFetch({});
  const r = await S().checkUpdate({ force: true });
  assert.equal(r, null);
  assert.match(S().toasts[0].text, /Vérification impossible : L'application ne répond pas/);
  assert.equal(S().updateChecking, false);
});

test("« Vérifier » force la vérification (force=1) et l'affichage normal ne la force pas", async () => {
  fakeFetch({ "GET /api/update": info({ available: false, latest: "1.1.0" }) });
  await S().checkUpdate({ quiet: true });
  await S().checkUpdate({ force: true });
  assert.deepEqual(calls, ["GET /api/update?force=0", "GET /api/update?force=1"]);
});

test("ignorer une version : mémorisée, fenêtre fermée, plus annoncée ; oubliée si on redemande", async () => {
  const table = {};
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true, writable: true,
    value: { getItem: (k) => table[k] ?? null, setItem: (k, v) => { table[k] = String(v); }, removeItem: (k) => { delete table[k]; } },
  });
  useStore.setState({ update: info(), updateOpen: true });
  S().skipUpdate();
  assert.equal(S().updateSkipped, "1.2.0");
  assert.equal(table["monteur.update.skip"], "1.2.0");
  assert.equal(S().updateOpen, false);
  assert.equal(announceable(S().update, S().updateSkipped), false);
  useStore.setState({ update: info({ latest: "1.3.0" }) });
  assert.equal(announceable(S().update, S().updateSkipped), true, "une version plus récente revient");
  S().clearSkippedUpdate();
  assert.equal(S().updateSkipped, "");
  assert.ok(!("monteur.update.skip" in table));
});

test("télécharger : progression, puis « prête » ; l'installation ne part pas toute seule", async () => {
  useStore.setState({ update: info() });
  fakeFetch({
    "POST /api/update/download": job({ status: "en_cours", progress: 0.1 }),
    "GET /api/jobs/j1": job({ status: "termine", progress: 1 }),
    "GET /api/update": info({ downloaded: true }),
  });
  const seen = [];
  const unsub = useStore.subscribe((s) => { if (s.updateJob) seen.push(s.updateJob.progress); });
  await S().startUpdateDownload();
  unsub();
  assert.ok(seen.includes(0.1), "la progression est suivie");
  assert.equal(S().updatePhase, "ready");
  assert.equal(S().update.downloaded, true);
  assert.equal(S().updateJob, null);
  assert.ok(!calls.some((c) => c.includes("/install")), "pas d'installation sans second clic");
});

test("télécharger : un échec du serveur s'affiche en clair et le bouton revient", async () => {
  useStore.setState({ update: info() });
  fakeFetch({ "POST /api/update/download": { status: 409, detail: "L'empreinte publiée ne correspond pas : téléchargement refusé." } });
  await S().startUpdateDownload();
  assert.equal(S().updatePhase, "idle");
  assert.equal(S().updateError, "L'empreinte publiée ne correspond pas : téléchargement refusé.");
});

test("télécharger : annulé, ce n'est pas une erreur", async () => {
  useStore.setState({ update: info() });
  fakeFetch({
    "POST /api/update/download": job({ status: "en_cours", progress: 0.3 }),
    "GET /api/jobs/j1": job({ status: "annulee", progress: 0.3 }),
    "GET /api/update": info(),
  });
  await S().startUpdateDownload();
  assert.equal(S().updatePhase, "idle");
  assert.equal(S().updateError, null);
  assert.equal(S().update.downloaded, false);
  assert.match(S().toasts[0].text, /annulé/);
});

test("installer : refusé tant qu'un export ou une tâche tourne, sans appeler le serveur", async () => {
  useStore.setState({ update: info({ downloaded: true }), updatePhase: "ready", jobs: { x: job({ id: "x", project_id: "p1", kind: "export", status: "en_cours" }) } });
  fakeFetch({ "POST /api/update/install": { ok: true } });
  const ok = await S().installUpdate();
  assert.equal(ok, false);
  assert.ok(!calls.some((c) => c.includes("/install")));
  assert.match(S().updateError, /export|tâche/);
  assert.equal(S().updatePhase, "ready");
});

test("installer : demande l'installation, puis l'application se ferme", async () => {
  useStore.setState({ update: info({ downloaded: true }), updatePhase: "ready" });
  fakeFetch({ "POST /api/update/install": { ok: true } });
  const ok = await S().installUpdate();
  assert.equal(ok, true);
  assert.deepEqual(calls, ["POST /api/update/install"]);
  assert.equal(S().updatePhase, "installing");
  assert.equal(S().updateError, null);
});

test("installer : le 409 du serveur s'affiche tel quel, et l'état réel est relu", async () => {
  useStore.setState({ update: info({ downloaded: true }), updatePhase: "ready" });
  fakeFetch({
    "POST /api/update/install": { status: 409, detail: "Une tâche est en cours : attends sa fin." },
    "GET /api/update": info({ downloaded: true }),   // le fichier est toujours là
  });
  const ok = await S().installUpdate();
  assert.equal(ok, false);
  assert.equal(S().updateError, "Une tâche est en cours : attends sa fin.");
  assert.equal(effectivePhase(S().update, S().updatePhase), "ready", "toujours prêt : on peut réessayer");
});

test("installer : installateur abîmé et supprimé (409) -> le bouton « Mettre à jour » revient", async () => {
  useStore.setState({ update: info({ downloaded: true }), updatePhase: "ready" });
  fakeFetch({
    "POST /api/update/install": { status: 409, detail: "L'installateur téléchargé est abîmé : il a été supprimé. Télécharge la mise à jour de nouveau." },
    "GET /api/update": info({ downloaded: false }),   // le serveur a supprimé le fichier
  });
  assert.equal(await S().installUpdate(), false);
  assert.match(S().updateError, /abîmé/);
  assert.equal(S().update.downloaded, false);
  assert.equal(effectivePhase(S().update, S().updatePhase), "idle", "plus « prête » : l'écran propose de retélécharger");
});

test("installer : 409 et serveur muet -> pas bloqué sur « prête » avec un fichier disparu", async () => {
  useStore.setState({ update: info({ downloaded: true }), updatePhase: "ready" });
  fakeFetch({ "POST /api/update/install": { status: 409, detail: "L'installateur n'est pas encore téléchargé." } });
  await S().installUpdate();
  assert.equal(effectivePhase(S().update, S().updatePhase), "idle");
});

test("installer : une coupure réseau laisse « prête » pour réessayer", async () => {
  useStore.setState({ update: info({ downloaded: true }), updatePhase: "ready" });
  fakeFetch({});
  assert.equal(await S().installUpdate(), false);
  assert.equal(S().updatePhase, "ready");
});

test("ignorer une version déjà téléchargée masque bandeau et pastille", () => {
  useStore.setState({ update: info({ downloaded: true }), updatePhase: "ready" });
  assert.equal(shouldShow(S().update, S().updateSkipped, S().updatePhase), true);
  S().skipUpdate();
  assert.equal(shouldShow(S().update, S().updateSkipped, S().updatePhase), false);
  assert.equal(shouldShow(S().update, S().updateSkipped, "downloading"), true, "un téléchargement en cours reste visible");
  assert.equal(shouldShow(null, "", "idle"), false);
});

test("vérifier à la main : une version plus récente que celle déjà téléchargée sort du blocage", async () => {
  useStore.setState({ update: info({ downloaded: true }), updatePhase: "ready" });
  fakeFetch({ "GET /api/update": info({ latest: "1.3.0", downloaded: false }) });
  await S().checkUpdate({ force: true });
  assert.equal(S().updatePhase, "idle");
  assert.equal(effectivePhase(S().update, S().updatePhase), "idle");
  // Une version toujours téléchargée reste « prête ».
  useStore.setState({ update: info({ downloaded: true }), updatePhase: "ready" });
  fakeFetch({ "GET /api/update": info({ downloaded: true }) });
  await S().checkUpdate({ force: true });
  assert.equal(S().updatePhase, "ready");
});

test("téléchargement raté fenêtre fermée : un message le dit ; fenêtre ouverte, l'erreur reste dans la fenêtre", async () => {
  useStore.setState({ update: info(), updateOpen: false });
  fakeFetch({ "POST /api/update/download": { status: 409, detail: "Pas assez de place sur le disque." } });
  await S().startUpdateDownload();
  assert.match(S().toasts.at(-1).text, /Téléchargement de la mise à jour impossible : Pas assez de place/);
  fresh();
  useStore.setState({ update: info(), updateOpen: true });
  await S().startUpdateDownload();
  assert.equal(S().toasts.length, 0);
  assert.match(S().updateError, /Pas assez de place/);
});

test("les notes en Markdown s'affichent en texte brut", () => {
  const md = "## Nouveautés\n\n- **Installateur Windows** : un fichier `MonteurIA-Setup-<version>.exe`\n* autre puce\n[la page](https://example.org/x)";
  const out = notesText(md);
  assert.ok(!/\*\*|`|#|\]\(/.test(out), out);
  assert.match(out, /^Nouveautés\n/);
  assert.match(out, /• Installateur Windows : un fichier MonteurIA-Setup-<version>\.exe/);
  assert.match(out, /• autre puce/);
  assert.ok(out.endsWith("la page"));
  assert.equal(notesText(notesText(md)), out, "sans effet une deuxième fois");
});

test("la fenêtre et le bandeau : ouvrir efface l'ancienne erreur, « Plus tard » masque le bandeau", () => {
  useStore.setState({ update: info(), updateError: "ancienne erreur" });
  S().openUpdate(true);
  assert.equal(S().updateOpen, true);
  assert.equal(S().updateError, null);
  S().openUpdate(false);
  assert.equal(S().updateOpen, false);
  S().hideUpdateBanner();
  assert.equal(S().updateHidden, true);
});
