// Reads config/scenarios.yaml (outside web/ -- read-only, this never writes
// there) and generates web/public/scenario_meta.json: {id, name, description}
// per scenario, fetched at runtime by Evidence exactly like the run JSONs.
// Source of truth for scenario descriptions stays the YAML; this script is
// the only place that reads it, so descriptions are never hand-duplicated
// into the frontend. Runs automatically via npm's predev/prebuild hooks.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import yaml from "js-yaml";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(WEB_ROOT, "..");

const yamlPath = path.join(REPO_ROOT, "config", "scenarios.yaml");
const outPath = path.join(WEB_ROOT, "public", "scenario_meta.json");

const raw = fs.readFileSync(yamlPath, "utf8");
const parsed = yaml.load(raw);

const meta = (parsed.scenarios || []).map((s) => ({
  id: s.id,
  name: s.name,
  description: String(s.description || "").trim().replace(/\s+/g, " "),
}));

fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(meta, null, 2) + "\n", "utf8");
console.log(`build-scenario-meta: wrote ${meta.length} scenario descriptions to ${path.relative(WEB_ROOT, outPath)}`);
