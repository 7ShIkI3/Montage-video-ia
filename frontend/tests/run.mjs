// Tests du store, sans navigateur : esbuild (déjà là avec Vite) assemble le test, Node l'exécute.
//   npm test
import { build } from "esbuild";
import { spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(tmpdir(), `monteur-store-test-${process.pid}.mjs`);
await build({ entryPoints: [join(here, "store.test.js")], bundle: true, platform: "node", format: "esm", outfile: out, logLevel: "warning",
  banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" } });
const r = spawnSync(process.execPath, ["--test", out], { stdio: "inherit" });
rmSync(out, { force: true });
process.exit(r.status ?? 1);
