// Registers node_resolve_hook.mjs as an ESM loader hook for the current
// Node process, then lets execution continue to the exporter. Passed to
// Node via `--import` so the hook is active before the frontend modules
// (with their Vite-style extensionless imports) are loaded.
import { register } from "node:module";
import { pathToFileURL } from "node:url";

register(new URL("./node_resolve_hook.mjs", import.meta.url), pathToFileURL("./"));
