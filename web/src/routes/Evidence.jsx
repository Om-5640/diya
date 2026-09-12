import { useEffect, useMemo, useState } from "react";
import { loadRun } from "../lib/loadRun";
import PageHeader from "../components/PageHeader";
import ScenarioSegmentedControl from "../components/ScenarioSegmentedControl";
import SectionLabel from "../components/SectionLabel";
import Metric from "../components/Metric";
import DispatchTimeline from "../components/DispatchTimeline";
import KpiReferenceTable from "../components/KpiReferenceTable";
import ReasonCard from "../components/ReasonCard";
import DisclosureToggle from "../components/DisclosureToggle";

const SCENARIOS = [
  { value: "S1", label: "S1 — Normal Week" },
  { value: "S2", label: "S2 — Monsoon Stretch" },
  { value: "S3", label: "S3 — Lean Season" },
];

const ALL_POLICIES = ["diesel_only", "rule_based", "mpc", "perfect_foresight"];

const HERO_IMPACT_METRICS = [
  { key: "diesel_l", label: "Diesel avoided", unit: "L", decimals: 1 },
  { key: "cost_total_inr", label: "Cost avoided", unit: "INR", decimals: 0 },
  { key: "co2_kg", label: "CO2 avoided", unit: "kg", decimals: 1 },
  { key: "critical_outage_hours", label: "Outage hours avoided", unit: "h", decimals: 1 },
];

const SUPPORTING_METRICS = [
  { key: "renewable_frac", label: "Renewable Frac", unit: "%", higherIsBetter: true, decimals: 1, pct: true },
  { key: "dg_starts", label: "Diesel Starts", unit: "", higherIsBetter: false, decimals: 0 },
];

const DEFAULT_K_UNCERTAINTY = 1.0; // no endpoint exposes this today; matches every scenario's current config default

function Slider({ label, value, onChange, min, max, step = 0.1, format }) {
  return (
    <label className="flex flex-col gap-1.5 text-body text-text-secondary min-w-[180px]">
      <span className="flex items-center justify-between">
        <span>{label}</span>
        <span className="tabular-nums text-text-primary">{format ? format(value) : value}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="accent-brand"
      />
    </label>
  );
}

/**
 * Evidence: PROVE. Scenario selector -> hero impact (rule_based-minus-mpc,
 * the exact same underlying KPI numbers, only subtracted for display) ->
 * supporting metrics -> a progressive comparison (MPC alone by default, a
 * toggle reveals the synchronized rule_based chart) -> Advanced (collapsed):
 * the 4-way table and the Stress Test panel, unchanged functionally.
 */
export default function Evidence() {
  const [scenario, setScenario] = useState("S2");
  const [scenarioMeta, setScenarioMeta] = useState([]);
  const [runs, setRuns] = useState({});
  const [showComparison, setShowComparison] = useState(false);
  const [hoveredIndex, setHoveredIndex] = useState(null);
  const [error, setError] = useState(null);

  const [costWeight, setCostWeight] = useState(1.0);
  const [co2Weight, setCo2Weight] = useState(1.0);
  const [reliabilityWeight, setReliabilityWeight] = useState(1.0);
  const [dieselPrice, setDieselPrice] = useState(null);
  const [kUncertainty, setKUncertainty] = useState(DEFAULT_K_UNCERTAINTY);
  const [isResolving, setIsResolving] = useState(false);
  const [resolveError, setResolveError] = useState(null);

  useEffect(() => {
    fetch("/scenario_meta.json")
      .then((r) => r.json())
      .then(setScenarioMeta)
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    setRuns({});
    setHoveredIndex(null);
    setError(null);
    setResolveError(null);
    setShowComparison(false);
    Promise.all(ALL_POLICIES.map((p) => loadRun(scenario, p).then((data) => [p, data])))
      .then((entries) => {
        if (cancelled) return;
        const map = {};
        entries.forEach(([p, data]) => {
          map[p] = data;
        });
        setRuns(map);
        const mpcKpi = map.mpc?.kpi;
        if (mpcKpi && mpcKpi.diesel_l > 0) {
          setDieselPrice(Number((mpcKpi.cost_fuel_inr / mpcKpi.diesel_l).toFixed(2)));
        }
        setKUncertainty(DEFAULT_K_UNCERTAINTY);
        setCostWeight(1.0);
        setCo2Weight(1.0);
        setReliabilityWeight(1.0);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [scenario]);

  const meta = useMemo(() => scenarioMeta.find((s) => s.id === scenario), [scenarioMeta, scenario]);

  const handleResolve = () => {
    setIsResolving(true);
    setResolveError(null);
    fetch("/api/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario_id: scenario,
        weights: { cost: costWeight, co2: co2Weight, reliability: reliabilityWeight },
        k_uncertainty: kUncertainty,
        diesel_price_inr_per_l: dieselPrice,
      }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`resolve HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setRuns((prev) => ({ ...prev, mpc: data }));
      })
      .catch((err) => {
        setResolveError(err.message);
      })
      .finally(() => setIsResolving(false));
  };

  if (error) {
    return <div className="p-8 text-body text-status-critical">Failed to load run data: {error}</div>;
  }

  const rbRun = runs.rule_based;
  const mpcRun = runs.mpc;
  const ready = rbRun && mpcRun && runs.diesel_only && runs.perfect_foresight;

  const mpcStep = ready && hoveredIndex != null ? mpcRun.steps[hoveredIndex] : null;
  const rbStep = ready && hoveredIndex != null ? rbRun.steps[hoveredIndex] : null;
  const diverges = mpcStep && rbStep && mpcStep.dg_on !== rbStep.dg_on;

  return (
    <div>
      <PageHeader
        title={meta ? `${meta.name}` : "Evidence"}
        meta={meta?.description}
        right={<ScenarioSegmentedControl options={SCENARIOS} value={scenario} onChange={setScenario} />}
      />

      {!ready ? (
        <div className="p-8 text-body text-text-secondary">Loading…</div>
      ) : (
        <>
          <div className="px-8 py-8 border-b border-border-subtle">
            <SectionLabel className="mb-4">Impact — MPC vs Rule-Based Baseline</SectionLabel>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
              {HERO_IMPACT_METRICS.map((m) => (
                <Metric
                  key={m.key}
                  label={m.label}
                  value={rbRun.kpi[m.key] - mpcRun.kpi[m.key]}
                  unit={m.unit}
                  decimals={m.decimals}
                  size="hero"
                  emphasize
                />
              ))}
            </div>
          </div>

          <div className="px-8 py-6 border-b border-border-subtle">
            <SectionLabel className="mb-4">Supporting Metrics</SectionLabel>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
              {SUPPORTING_METRICS.map((m) => (
                <Metric
                  key={m.key}
                  label={m.label}
                  value={m.pct ? mpcRun.kpi[m.key] * 100 : mpcRun.kpi[m.key]}
                  unit={m.unit}
                  decimals={m.decimals}
                  comparisonValue={m.pct ? rbRun.kpi[m.key] * 100 : rbRun.kpi[m.key]}
                  higherIsBetter={m.higherIsBetter}
                  size="supporting"
                />
              ))}
            </div>
          </div>

          <div className="px-8 py-8 border-b border-border-subtle">
            <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
              <SectionLabel>Comparison</SectionLabel>
              <label className="flex items-center gap-2 text-body text-text-secondary cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={showComparison}
                  onChange={(e) => setShowComparison(e.target.checked)}
                  className="accent-brand"
                />
                Compare with rule-based baseline
              </label>
            </div>

            <div className="flex flex-col gap-2">
              <div>
                <SectionLabel className="mb-2">Model-Predictive Control</SectionLabel>
                <DispatchTimeline
                  steps={mpcRun.steps}
                  height={280}
                  hoveredIndex={hoveredIndex}
                  onHoverIndex={setHoveredIndex}
                />
              </div>

              {showComparison && (
                <div>
                  <SectionLabel className="mb-2">Rule-Based Baseline</SectionLabel>
                  <DispatchTimeline
                    steps={rbRun.steps}
                    height={280}
                    hoveredIndex={hoveredIndex}
                    onHoverIndex={setHoveredIndex}
                  />
                </div>
              )}

              <div className="mt-2 border border-border-subtle rounded-md p-4 min-h-[96px] bg-bg-surface">
                {!showComparison ? (
                  mpcStep ? (
                    <ReasonCard code={mpcStep.reason_code} text={mpcStep.reason_text} timestamp={mpcStep.t} />
                  ) : (
                    <div className="text-small text-text-tertiary">Hover a point on the chart to see why.</div>
                  )
                ) : mpcStep && rbStep ? (
                  <div>
                    <div className="text-small font-medium text-text-tertiary mb-3">
                      {diverges ? "Why did they differ?" : "Both policies agreed this hour"}
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <ReasonCard title="MPC" code={mpcStep.reason_code} text={mpcStep.reason_text} timestamp={mpcStep.t} />
                      <ReasonCard
                        title="Rule-Based"
                        code={rbStep.reason_code}
                        text={rbStep.reason_text}
                        timestamp={rbStep.t}
                      />
                    </div>
                  </div>
                ) : (
                  <div className="text-small text-text-tertiary">Hover a point on either chart to compare reasoning.</div>
                )}
              </div>
            </div>
          </div>

          <div className="px-8 py-8">
            <DisclosureToggle label="Advanced analysis" defaultOpen={false}>
              <div className="flex flex-col gap-8">
                <div>
                  <SectionLabel className="mb-3">4-Way KPI Reference</SectionLabel>
                  <KpiReferenceTable runs={runs} />
                </div>

                <div>
                  <SectionLabel className="mb-4">Stress Test</SectionLabel>
                  <div className="border border-border-subtle rounded-md p-6 flex flex-col gap-6 bg-bg-surface-sunken">
                    <div className="flex flex-wrap gap-6">
                      <Slider
                        label="Cost weight"
                        value={costWeight}
                        onChange={setCostWeight}
                        min={0.5}
                        max={2.0}
                        format={(v) => v.toFixed(2)}
                      />
                      <Slider
                        label="CO2 weight"
                        value={co2Weight}
                        onChange={setCo2Weight}
                        min={0.5}
                        max={2.0}
                        format={(v) => v.toFixed(2)}
                      />
                      <Slider
                        label="Reliability weight"
                        value={reliabilityWeight}
                        onChange={setReliabilityWeight}
                        min={0.5}
                        max={2.0}
                        format={(v) => v.toFixed(2)}
                      />
                      <Slider
                        label="k_uncertainty"
                        value={kUncertainty}
                        onChange={setKUncertainty}
                        min={1.0}
                        max={2.0}
                        format={(v) => v.toFixed(2)}
                      />
                      <label className="flex flex-col gap-1.5 text-body text-text-secondary min-w-[160px]">
                        <span>Diesel price (INR/L)</span>
                        <input
                          type="number"
                          step="0.1"
                          value={dieselPrice ?? ""}
                          onChange={(e) => setDieselPrice(Number(e.target.value))}
                          className="bg-bg-surface border border-border-subtle rounded-md px-2 py-1.5 text-text-primary tabular-nums w-28"
                        />
                      </label>
                    </div>

                    <div className="flex items-center gap-4 flex-wrap">
                      <button
                        type="button"
                        onClick={handleResolve}
                        disabled={isResolving || dieselPrice == null}
                        className="text-body text-bg-surface bg-brand hover:opacity-90 rounded-md px-4 py-2 transition-opacity duration-150 ease-out disabled:opacity-60"
                      >
                        {isResolving ? "Re-solving…" : "Re-solve"}
                      </button>
                      {isResolving && (
                        <span className="text-small text-text-tertiary">
                          Re-solving… (a full 168-hour re-optimization, this can take up to ~60s)
                        </span>
                      )}
                      {resolveError && !isResolving && (
                        <span className="text-small text-status-critical">Re-solve failed: {resolveError}</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </DisclosureToggle>
          </div>
        </>
      )}
    </div>
  );
}
