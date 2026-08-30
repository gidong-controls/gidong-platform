import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const indexPath = fileURLToPath(new URL("../dist/index.html", import.meta.url));
const html = await readFile(indexPath, "utf8");

const checks = [
  [
    /<script defer src="\/assets\/index-[^"]+\.js"><\/script>/,
    "production JavaScript must use the verified deferred classic entry",
  ],
  [
    /<link rel="stylesheet" href="\/assets\/index-[^"]+\.css">/,
    "production CSS must not use the incompatible crossorigin attribute",
  ],
  [/<script src="\/boot-guard\.js"><\/script>/, "boot guard must be present"],
];

for (const [pattern, message] of checks) {
  if (!pattern.test(html)) {
    throw new Error(`${message}: ${indexPath}`);
  }
}

if (/type="module"|rel="stylesheet" crossorigin/.test(html)) {
  throw new Error(`incompatible production asset attributes remain: ${indexPath}`);
}

console.log(`Verified ControlStudio production entry in ${webRoot}`);
