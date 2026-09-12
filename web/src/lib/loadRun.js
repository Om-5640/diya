/**
 * Loads a precomputed RunResult from the static JSON in public/runs/.
 * Phase 1B works from these files alone -- never calls the backend API.
 */
export async function loadRun(scenarioId, policy) {
  const res = await fetch(`/runs/${scenarioId}_${policy}.json`);
  if (!res.ok) {
    throw new Error(`Failed to load ${scenarioId}_${policy}.json (HTTP ${res.status})`);
  }
  return res.json();
}
