// Executes the ACTUAL frontend data modules with Node and dumps their
// exported values as JSON.
//
// Why: PineLabs_Frontend/src/data/*.js are plain ES modules, but
// binSeries.js and sopData.js don't just declare static arrays — they
// generate hundreds of rows at import time (a deterministic PRNG
// `mulberry32`, `Array.from` loops, string interpolation; see
// sopData.js's `bulkSheets`/`bulk` and binSeries.js's `bulkBins`).
// Re-implementing that generation logic in Python would risk silently
// diverging from what the running frontend actually computes. Running
// the real files with Node and serializing the result is the only way
// to guarantee byte-for-byte fidelity with "the existing frontend mock
// data" — no dataset is retyped or reconstructed here.
//
// Usage:
//   node export_frontend_data.mjs <path-to-PineLabs_Frontend/src/data>
//
// Writes a single JSON object to stdout:
//   { binSeries, merchants, commonEscalation, users, revisions }
import path from "node:path";
import { pathToFileURL } from "node:url";

const dataDir = process.argv[2];

if (!dataDir) {
  console.error("Usage: node export_frontend_data.mjs <path-to-frontend-src-data>");
  process.exit(1);
}

async function loadModule(filename) {
  const fullPath = path.join(dataDir, filename);
  return import(pathToFileURL(fullPath).href);
}

const [binSeriesModule, sopDataModule, usersModule, revisionsModule] = await Promise.all([
  loadModule("binSeries.js"),
  loadModule("sopData.js"),
  loadModule("users.js"),
  loadModule("revisions.js"),
]);

const output = {
  binSeries: binSeriesModule.binSeries,
  merchants: sopDataModule.merchants,
  commonEscalation: sopDataModule.commonEscalation,
  users: usersModule.users,
  revisions: revisionsModule.revisions,
};

process.stdout.write(JSON.stringify(output));
