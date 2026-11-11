// Tests du store et de la logique pure, sans navigateur : esbuild (déjà là avec Vite) assemble chaque test, Node les exécute.
//   npm test
import { build } from "esbuild";
import { spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const files = ["store.test.js", "update.test.js"];
const outs = [];
for (const f of files) {
  const out = join(tmpdir(), `monteur-${f.replace(/\W+/g, "-")}-${process.pid}.mjs`);
  await build({ entryPoints: [join(here, f)], bundle: true, platform: "node", format: "esm", outfile: out, logLevel: "warning",
    banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" } });
  outs.push(out);
}
const r = spawnSync(process.execPath, ["--test", ...outs], { stdio: "inherit", env: { ...process.env, FRONTEND_DIR: join(here, "..") } });
for (const out of outs) rmSync(out, { force: true });
process.exit(r.status ?? 1);
