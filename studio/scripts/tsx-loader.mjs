import { readFile, access } from "node:fs/promises";
import { transformWithEsbuild } from "vite";

export async function resolve(specifier, context, next) {
  if (specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)) {
    for (const extension of [".ts", ".tsx"]) {
      const url = new URL(specifier + extension, context.parentURL);
      try {
        await access(url);
        return { url: url.href, shortCircuit: true };
      } catch { /* Try the next source extension. */ }
    }
  }
  return next(specifier, context);
}

export async function load(url, context, next) {
  if (!url.endsWith(".tsx")) return next(url, context);
  const source = await readFile(new URL(url), "utf8");
  const transformed = await transformWithEsbuild(source, new URL(url).pathname, { loader: "tsx", jsx: "automatic" });
  return { format: "module", shortCircuit: true, source: transformed.code };
}
