import { readFile, readdir } from "node:fs/promises";
import { extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const checkedExtensions = new Set([".css", ".html", ".json", ".ts", ".tsx"]);
const excluded = new Set(["dist", "node_modules", "reproducibility"]);
const findings = [];

async function visit(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (excluded.has(entry.name)) continue;
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      await visit(path);
      continue;
    }
    if (!checkedExtensions.has(extname(entry.name))) continue;
    const source = await readFile(path, "utf8");
    source.split("\n").forEach((line, index) => {
      if (/\s+$/.test(line)) findings.push(`${relative(root, path)}:${index + 1}: trailing whitespace`);
      if (/\bconsole\.(log|debug)\b/.test(line)) findings.push(`${relative(root, path)}:${index + 1}: debug logging`);
    });
  }
}

await visit(root);
if (findings.length) {
  findings.forEach((finding) => console.error(finding));
  process.exitCode = 1;
} else {
  console.log("studio lint: 0 findings");
}
