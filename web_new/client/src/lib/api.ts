export const API_BASE = "https://diya-api-ibwb.onrender.com";
const REQUEST_TIMEOUT_MS = 45_000;

export class ApiError extends Error {
  status?: number;
  detail?: string;
  constructor(message: string, status?: number, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export type SiteSummary = {
  site_id: string;
  display_name: string;
  lat: number;
  lon: number;
  created_at: string;
  is_seed: boolean;
  pv?: { capacity_kwp?: number };
  battery?: { capacity_kwh?: number };
  diesel?: { rated_kw?: number };
  k_uncertainty?: number;
};

export type StepResult = {
  t: string;
  pv_kw: number;
  wind_kw: number;
  dg_kw: number;
  dg_on: number;
  batt_kw: number;
  soc_kwh: number;
  soc_pct: number;
  load_critical_kw: number;
  load_essential_kw: number;
  load_deferrable_kw: number;
  unserved_critical_kwh: number;
  unserved_essential_kwh: number;
  unserved_deferrable_kwh: number;
  curtailed_kwh: number;
  fuel_l: number;
  reason_code: string;
  reason_text: string;
};

export type SiteOverview = {
  site: SiteSummary;
  current_step: StepResult;
  status: "live_only" | "full";
  has_precomputed_runs: boolean;
};

export type DispatchResponse = {
  site_id: string;
  scenario_id: string;
  policy: string;
  range_hours: number;
  source: "live" | "precomputed";
  steps: StepResult[];
};

export type ScenarioSummary = { scenario_id: string; name: string };
export type DeltaValue = { raw: number; avoided: number };
export type KPI = Record<string, number>;
export type RunResult = {
  scenario_id: string;
  policy: "diesel_only" | "rule_based" | "mpc" | "perfect_foresight" | string;
  site_id: string;
  site_name: string;
  steps: StepResult[];
  kpi: KPI;
  provenance: Record<string, unknown>;
  notes?: string;
};
export type EvidenceResponse = {
  site_id: string;
  scenario_id: string;
  description: string | null;
  rule_based: RunResult;
  mpc: RunResult;
  diesel_only: RunResult;
  perfect_foresight: RunResult;
  delta: {
    diesel_l: DeltaValue;
    cost_total_inr: DeltaValue;
    co2_kg: DeltaValue;
    critical_outage_hours: DeltaValue;
    renewable_frac: DeltaValue;
    dg_starts: DeltaValue;
  };
};

export type ResolveRequest = {
  scenario_id: string;
  weights: { cost: number; co2: number; reliability: number };
  k_uncertainty: number;
  diesel_price_inr_per_l: number;
};

export type SiteUpdateRequest = {
  name?: string;
  lat?: number;
  lon?: number;
  elevation_m?: number;
  timezone?: string;
  pv?: { capacity_kwp: number };
  wind?: { rated_kw: number };
  battery?: { capacity_kwh: number };
  diesel?: { rated_kw: number };
  economics?: { diesel_price_inr_per_l?: number; voll_critical_inr_per_kwh?: number };
  horizon_hours?: number;
  reserve_hours?: number;
  k_uncertainty?: number;
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function readableValue(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const messages = value.map(item => {
      const entry = record(item);
      return readableValue(entry.msg) ?? readableValue(entry.message) ?? readableValue(entry.detail) ?? readableValue(item);
    }).filter((item): item is string => !!item);
    return messages.length ? messages.join(" ") : undefined;
  }
  if (value && typeof value === "object") {
    const item = record(value);
    for (const key of ["detail", "message", "error", "title"]) {
      const nested = readableValue(item[key]);
      if (nested) return nested;
    }
  }
  return undefined;
}

export function safeText(value: unknown, fallback = "No data available.") {
  return readableValue(value) ?? fallback;
}

export function getApiErrorMessage(error: unknown, fallback = "Something went wrong. Please try again.") {
  const item = record(error);
  const response = record(item.response);
  const responseData = record(response.data);
  const nativeMessage = error instanceof Error ? error.message : undefined;
  return readableValue(responseData.detail) ?? readableValue(item.detail) ?? readableValue(item.message) ?? nativeMessage ?? readableValue(responseData.message) ?? readableValue(responseData.error) ?? readableValue(error) ?? fallback;
}

function responseErrorMessage(body: unknown, status: number) {
  return getApiErrorMessage(body, `Request failed (${status})`);
}

async function parseResponse(response: Response) {
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try { body = JSON.parse(text); } catch { body = text; }
  }
  if (!response.ok) {
    const detail = responseErrorMessage(body, response.status);
    throw new ApiError(detail, response.status, detail);
  }
  return body;
}

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, REQUEST_TIMEOUT_MS);
  const externalSignal = options.signal;
  const abortFromCaller = () => controller.abort();
  externalSignal?.addEventListener("abort", abortFromCaller, { once: true });
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
    });
    return parseResponse(response) as Promise<T>;
  } catch (error) {
    if (timedOut) throw new ApiError("The DIYA service took too long to respond. Please try again.");
    throw error;
  } finally {
    window.clearTimeout(timeout);
    externalSignal?.removeEventListener("abort", abortFromCaller);
  }
}

export function apiGet<T>(path: string, options?: RequestInit) { return apiRequest<T>(path, options); }
export function apiPost<T>(path: string, body: unknown, options?: RequestInit) { return apiRequest<T>(path, { ...options, method: "POST", body: JSON.stringify(body) }); }
export function apiPatch<T>(path: string, body: unknown, options?: RequestInit) { return apiRequest<T>(path, { ...options, method: "PATCH", body: JSON.stringify(body) }); }
export function apiDelete<T>(path: string, options?: RequestInit) { return apiRequest<T>(path, { ...options, method: "DELETE" }); }

export function formatNumber(value: unknown, maximumFractionDigits = 2) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "No data available.";
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits, minimumFractionDigits: 0 }).format(value);
}

export function formatDate(value: unknown) {
  if (!value || typeof value !== "string" && typeof value !== "number") return "Timestamp unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return safeText(value, "Timestamp unavailable");
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
}

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function displayValue(value: unknown, suffix = "") {
  if (value === null || value === undefined || value === "") return "No data available.";
  if (typeof value === "number") return `${formatNumber(value)}${suffix}`;
  return safeText(value);
}

export function normalizeStep(value: unknown, context = "dispatch step"): StepResult {
  const item = record(value);
  const required = ["t", "pv_kw", "wind_kw", "dg_kw", "dg_on", "batt_kw", "soc_kwh", "soc_pct", "load_critical_kw", "load_essential_kw", "load_deferrable_kw", "unserved_critical_kwh", "unserved_essential_kwh", "unserved_deferrable_kwh", "curtailed_kwh", "fuel_l", "reason_code", "reason_text"];
  if (required.some(key => item[key] === undefined || item[key] === null)) throw new ApiError(`The API returned an incomplete ${context}.`);
  return item as StepResult;
}

export function normalizeSiteSummary(value: unknown): SiteSummary {
  const item = record(value);
  if (typeof item.site_id !== "string" || typeof item.display_name !== "string") throw new ApiError("The API returned an invalid site summary.");
  return item as SiteSummary;
}

export function normalizeOverview(value: unknown): SiteOverview {
  const item = record(value);
  return { site: normalizeSiteSummary(item.site), current_step: normalizeStep(item.current_step, "overview step"), status: item.status === "full" ? "full" : "live_only", has_precomputed_runs: item.has_precomputed_runs === true };
}

export function normalizeDispatch(value: unknown): DispatchResponse {
  const item = record(value);
  if (!Array.isArray(item.steps)) throw new ApiError("The API returned an invalid dispatch response.");
  return { site_id: String(item.site_id ?? ""), scenario_id: String(item.scenario_id ?? ""), policy: String(item.policy ?? ""), range_hours: Number(item.range_hours ?? 0), source: item.source === "live" ? "live" : "precomputed", steps: item.steps.map((step, index) => normalizeStep(step, `dispatch step ${index + 1}`)) };
}

export function normalizeScenarios(value: unknown): ScenarioSummary[] {
  if (!Array.isArray(value)) throw new ApiError("The API returned an invalid scenario list.");
  return value.map(item => { const row = record(item); if (typeof row.scenario_id !== "string" || typeof row.name !== "string") throw new ApiError("The API returned an invalid scenario entry."); return row as ScenarioSummary; });
}

export function normalizeRunResult(value: unknown): RunResult {
  const item = record(value);
  if (!Array.isArray(item.steps) || !item.kpi || !item.provenance) throw new ApiError("The API returned an invalid run result.");
  return { scenario_id: String(item.scenario_id ?? ""), policy: String(item.policy ?? ""), site_id: String(item.site_id ?? ""), site_name: String(item.site_name ?? ""), steps: item.steps.map((step, index) => normalizeStep(step, `run step ${index + 1}`)), kpi: record(item.kpi) as KPI, provenance: record(item.provenance), notes: typeof item.notes === "string" ? item.notes : undefined };
}

export function normalizeEvidence(value: unknown): EvidenceResponse {
  const item = record(value);
  const delta = record(item.delta);
  const deltaKeys = ["diesel_l", "cost_total_inr", "co2_kg", "critical_outage_hours", "renewable_frac", "dg_starts"];
  if (typeof item.description !== "string" && item.description !== null) throw new ApiError("The API returned an invalid evidence description.");
  if (deltaKeys.some(key => !record(delta[key]).raw && record(delta[key]).raw !== 0)) throw new ApiError("The API returned an incomplete evidence delta.");
  const normalizeDelta = (key: string) => { const row = record(delta[key]); return { raw: Number(row.raw), avoided: Number(row.avoided) }; };
  return { site_id: String(item.site_id ?? ""), scenario_id: String(item.scenario_id ?? ""), description: item.description as string | null, rule_based: normalizeRunResult(item.rule_based), mpc: normalizeRunResult(item.mpc), diesel_only: normalizeRunResult(item.diesel_only), perfect_foresight: normalizeRunResult(item.perfect_foresight), delta: { diesel_l: normalizeDelta("diesel_l"), cost_total_inr: normalizeDelta("cost_total_inr"), co2_kg: normalizeDelta("co2_kg"), critical_outage_hours: normalizeDelta("critical_outage_hours"), renewable_frac: normalizeDelta("renewable_frac"), dg_starts: normalizeDelta("dg_starts") } };
}

export function persistLastSiteId(siteId: string) {
  try { if (typeof window !== "undefined") window.localStorage.setItem("diya:lastSiteId", siteId); } catch (error) { console.warn("[DIYA] Could not persist last site ID", error); }
}

export function clearLastSiteId() {
  try { if (typeof window !== "undefined") window.localStorage.removeItem("diya:lastSiteId"); } catch (error) { console.warn("[DIYA] Could not clear last site ID", error); }
}
