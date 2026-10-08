// Node ESM resolution hook for the one-off mock-data exporter.
//
// Why this exists: the frontend's data modules (e.g. binSeries.js) use
// Vite-style extensionless relative imports such as
//   import { instances } from './sopData'
// Vite's dev server/bundler resolves these by trying `.js`/`.jsx`/etc.,
// but Node's native ESM loader does NOT — it requires the explicit
// extension and otherwise throws ERR_MODULE_NOT_FOUND. Since the
// exporter runs the real frontend files through raw Node (to guarantee
// byte-for-byte fidelity with what the app computes), we register this
// hook to reproduce Vite's extension resolution for relative specifiers.
//
// Scope is deliberately narrow: it only kicks in when the default
// resolver fails on a relative specifier that has no extension, and it
// only tries the handful of JS extensions Vite would. The frontend
// source is never modified.
import { stat } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

const CANDIDATE_EXTENSIONS = [".js", ".mjs", ".jsx", ".cjs"];

async function isFile(url) {
  try {
    return (await stat(fileURLToPath(url))).isFile();
  } catch {
    return false;
  }
}

export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    const isRelative = specifier.startsWith("./") || specifier.startsWith("../");
    const hasExtension = /\.[^/]+$/.test(specifier);
    if (!isRelative || hasExtension || !context.parentURL) {
      throw error;
    }

    for (const ext of CANDIDATE_EXTENSIONS) {
      const candidate = new URL(specifier + ext, context.parentURL);
      if (await isFile(candidate)) {
        return { url: candidate.href, shortCircuit: true };
      }
    }
    throw error;
  }
}
