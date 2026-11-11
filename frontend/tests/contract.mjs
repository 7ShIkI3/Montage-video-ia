// Contrôle de contrat de l'interface, sans dépendance :
//   npm run check
// Il lit frontend/src et échoue si un identifiant, un nom de classe, un couplage de hauteur ou une règle de rendu
// que la refonte « Studio » s'est interdit de toucher a bougé. Il ne lance ni navigateur ni serveur.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "src");

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}
const read = (p) => readFileSync(p, "utf8");
const files = walk(src);
const code = files.filter((f) => /\.(tsx|ts)$/.test(f));
const tsx = files.filter((f) => f.endsWith(".tsx"));
const stylesCss = join(src, "styles.css");
const sheets = [stylesCss, ...files.filter((f) => /[\\/]styles[\\/][^\\/]+\.css$/.test(f)).sort()];
const allCode = code.map(read).join("\n");
const allTsx = tsx.map(read).join("\n");
const allCss = sheets.map(read).join("\n");
const rel = (p) => relative(root, p).replace(/\\/g, "/");

const errors = [];
const fail = (msg) => errors.push(msg);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/** Retire les commentaires CSS pour que les contrôles de règles ne lisent que du code. */
const stripCss = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "");

// 1. Les 9 data-testid et les 8 id de l'interface
const TESTIDS = ["director-chat", "chat-vision", "chat-live", "multi-note", "live-director", "sel-count", "thumbs-toggle", "source-monitor", "pitch"];
for (const t of TESTIDS) {
  if (!new RegExp(`data-testid=(?:"${esc(t)}"|\\{["'\`]${esc(t)}["'\`]\\})`).test(allTsx)) fail(`data-testid « ${t} » absent des .tsx`);
}
const IDS = ["comment-box", "source-title", "lib-title", "claude", "transcription", "voix", "serveur", "systeme", "apropos"];
for (const i of IDS) {
  if (!new RegExp(`[\\s{]id=(?:"${esc(i)}"|\\{["'\`]${esc(i)}["'\`]\\})`).test(allTsx)) fail(`id « ${i} » absent des .tsx`);
}

// 2. Store : ordre des onglets droite, valeurs de LeftTab
const store = read(join(src, "store.ts"));
const order = /RIGHT_TAB_ORDER\s*:\s*RightTab\[\]\s*=\s*\[([^\]]*)\]/.exec(store);
const wantOrder = ["consigne", "montage", "plan", "habillage", "audio", "qualite", "export", "texte", "recherche"];
if (!order) fail("RIGHT_TAB_ORDER introuvable dans store.ts");
else if (JSON.stringify([...order[1].matchAll(/"([^"]+)"/g)].map((m) => m[1])) !== JSON.stringify(wantOrder)) fail(`RIGHT_TAB_ORDER doit valoir exactement ${JSON.stringify(wantOrder)}`);
const leftType = /export type LeftTab\s*=([^;]+);/.exec(store);
if (!leftType) fail("type LeftTab introuvable dans store.ts");
else for (const v of ["rushes", "ia", "audio", "texte", "effets", "versions", "modeles", "marque"]) {
  if (!new RegExp(`"${v}"`).test(leftType[1])) fail(`LeftTab ne contient plus « ${v} »`);
}

// 3. Noms de classes à ne jamais renommer : présents dans une feuille de style ou dans un .tsx
const hasClass = (name) => new RegExp(`\\.${esc(name)}(?![\\w-])`).test(allCss) || new RegExp(`(?<![\\w-])${esc(name)}(?![\\w-])`).test(allTsx);
const CLASSES = [
  "card-menu", "more", "menu-open", "over", "on", "busy", "badge", "sel", "left-closed", "right-closed", "resizing-v", "resizing-h", "slipping",
  "splitter", "tab-head", "tab-body", "right-body", "rail", "rail-panel", "rtabs", "timeline-scroll", "ruler", "track-video", "clip", "handle",
  "left", "right", "frame", "ov",
];
for (const c of CLASSES) if (!hasClass(c)) fail(`classe « .${c} » absente des feuilles de style et des .tsx`);
// Familles construites par le JSX (frame-16x9, s-ouvert, cap-9x16…) : le préfixe doit exister dans une feuille de style.
const PREFIXES = ["frame-", "s-", "cap-", "pos-", "ti-", "k-", "c-", "anim-", "fx-", "ov-"];
for (const p of PREFIXES) if (!new RegExp(`\\.${esc(p)}[a-z0-9_]`, "i").test(allCss)) fail(`aucune classe « .${p}* » dans les feuilles de style`);

// 4. Couplages de hauteur de la timeline : CLIP_H = 74 <-> .track-video = 86 px
if (!/\bCLIP_H\s*=\s*74\b/.test(allCode)) fail("CLIP_H doit rester égal à 74");
let trackH = null;
for (const f of sheets) {   // styles.css puis styles/timeline.css : la dernière déclaration gagne
  for (const m of stripCss(read(f)).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!m[1].split(",").some((sel) => /^\s*(\.track\.track-video|\.track-video)\s*$/.test(sel))) continue;
    const h = /(?:^|[;\s])height\s*:\s*([^;]+)/.exec(m[2]);
    if (h) trackH = h[1].trim();
  }
}
if (trackH !== "86px") fail(`.track-video doit mesurer 86px (trouvé : ${trackH ?? "aucune déclaration"})`);

// 5. Contrats de drag, d'événement et d'ancres
for (const [token, why] of [["MEDIA_DRAG_TYPE", "type de glisser des médias"], ["templates-changed", "événement des modèles"], ["#/reglages", "ancre des réglages"], ["#/p/", "ancre d'un projet"]]) {
  if (!allCode.includes(token)) fail(`« ${token} » (${why}) a disparu de src/`);
}

// 6. Tailles de texte : aucun font-size / font en px sous 10,5 (les unités relatives au cadre sont ignorées)
for (const f of sheets) {
  const css = stripCss(read(f));
  for (const m of css.matchAll(/(?:^|[;{\s])(font-size|font)\s*:\s*([^;}]+)/g)) {
    let v = m[2].replace(/var\([^)]*\)/g, " ").trim();
    if (/[()]/.test(v) || /\d(?:cq(?:w|h|i|b|min|max)?|em|rem|vw|vh|%)(?![a-z])/.test(v)) continue;   // clamp(), max(), unités relatives
    const px = m[1] === "font-size" ? /^(\d+(?:\.\d+)?)px$/.exec(v) : /(?:^|\s)(\d+(?:\.\d+)?)px(?:\/\S+)?(?:\s|$)/.exec(v);
    if (px && parseFloat(px[1]) < 10.5) fail(`${rel(f)} : ${m[1]}: ${m[2].trim()} (minimum 10,5px)`);
  }
}

// 7. Icônes : chaque nom littéral passé à <Icon> ou à une prop/propriété « icon » existe dans icons.tsx
const iconsSrc = read(join(src, "icons.tsx"));
const paths = /const PATHS[^=]*=\s*\{([\s\S]*?)\n\};/.exec(iconsSrc);
const known = new Set(paths ? [...paths[1].matchAll(/^\s*"?([a-z0-9-]+)"?\s*:/gm)].map((m) => m[1]) : []);
if (!known.size) fail("PATHS introuvable dans icons.tsx");
const used = new Map();
/** Valeurs possibles d'une expression d'icône : le littéral de tête, et ceux qui suivent un « ? » ou un « : » (pas ceux d'une comparaison). */
const literals = (expr) => [...expr.matchAll(/(?:^|[?:])\s*"([a-z0-9-]+)"/g)].map((m) => m[1]);
const note = (name, f) => { if (/^[a-z][a-z0-9-]*$/.test(name)) used.set(`${name}\u0000${rel(f)}`, [name, rel(f)]); };
for (const f of tsx) {
  const s = read(f);
  for (const m of s.matchAll(/<Icon\b[^>]*?\bname=(?:"([^"]+)"|\{([^}]*)\})/g)) {
    if (m[1]) note(m[1], f); else for (const v of literals(m[2].trim())) note(v, f);
  }
  for (const m of s.matchAll(/\bicon\s*[:=]\s*\{?([^,}\n]*)/g)) for (const v of literals(m[1].trim())) note(v, f);
}
for (const f of code.filter((x) => x.endsWith(".ts"))) {
  for (const m of read(f).matchAll(/\bicon\s*:\s*([^,}\n]*)/g)) for (const v of literals(m[1].trim())) note(v, f);
}
for (const [name, f] of used.values()) if (!known.has(name)) fail(`${f} : icône « ${name} » absente de icons.tsx`);

// 8. Feuilles de la coque : pas de flou de fond, rien sur le cadre vidéo, pas de couleur en dur
const own = (n) => sheets.find((f) => f.split("\\").join("/").endsWith(`/styles/${n}`));
for (const n of ["stage.css", "shell.css", "ui.css"]) {
  const f = own(n);
  if (!f) { fail(`styles/${n} absente`); continue; }
  const css = stripCss(read(f));
  if (/backdrop-filter/.test(css)) fail(`${rel(f)} : backdrop-filter interdit`);
  if (/#[0-9a-f]{3,8}\b/i.test(css) || /\brgba?\(/i.test(css)) fail(`${rel(f)} : couleur en dur (utiliser un jeton de :root)`);
  if (n === "stage.css" && /(?:^|[\s,>+~{}])\.(?:frame|fxwrap)(?![\w-])/.test(css)) fail(`${rel(f)} : aucune règle sur le cadre vidéo ni sur sa couche d'effets`);
}

// 9. Mises à jour : notes en texte brut, version jamais écrite en dur, une seule source de version
if (/dangerouslySetInnerHTML|\.innerHTML\s*=|insertAdjacentHTML/.test(allCode)) fail("aucun HTML injecté dans src/ (les notes de version sont du texte brut)");
const updateTsx = read(join(src, "components", "Update.tsx"));
if (!/\{notes \|\| "Pas de notes/.test(updateTsx)) fail("Update.tsx : les notes de version doivent être rendues comme texte ({notes})");
if (!/white-space:\s*pre-wrap/.test(stripCss(read(join(src, "styles", "update.css"))))) fail("styles/update.css : .update-notes doit garder les retours à la ligne (white-space: pre-wrap)");
if (!/SKIP_KEY\s*=\s*"monteur\.update\.skip"/.test(read(join(src, "update.ts")))) fail("update.ts : la clé de la version ignorée doit rester « monteur.update.skip »");
if (!/mise_a_jour\s*:/.test(store)) fail("store.ts : KIND_LABELS doit nommer le type de tâche « mise_a_jour »");
for (const f of tsx) {
  const hit = /["'`]v?\d+\.\d+\.\d+["'`]/.exec(read(f));
  if (hit) fail(`${rel(f)} : numéro de version en dur (${hit[0]}) ; la version affichée vient de l'API`);
}
{
  const pkg = JSON.parse(read(join(root, "package.json")));
  let backend = null;
  try { backend = /^__version__\s*=\s*"([^"]+)"/m.exec(readFileSync(join(root, "..", "backend", "app", "version.py"), "utf8"))?.[1] ?? null; } catch { /* dépôt incomplet : pas de comparaison */ }
  if (backend && pkg.version !== backend) fail(`package.json (${pkg.version}) doit suivre backend/app/version.py (${backend})`);
}

if (errors.length) {
  console.error(`Contrat de l'interface : ${errors.length} écart(s)`);
  for (const e of errors) console.error(`  - ${e}`);
  process.exit(1);
}
console.log(`Contrat de l'interface respecté (${TESTIDS.length} data-testid, ${IDS.length} id, ${CLASSES.length + PREFIXES.length} classes, ${sheets.length} feuilles de style).`);
