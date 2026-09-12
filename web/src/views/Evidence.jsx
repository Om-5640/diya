import { useEffect, useState } from "react";
import { loadRun } from "../lib/loadRun";
import SegmentedControl from "../components/SegmentedControl";
import SectionLabel from "../components/SectionLabel";
import KpiCard from "../components/KpiCard";
import DispatchTimeline from "../components/DispatchTimeline";
import KpiReferenceTable from "../components/KpiReferenceTable";
import ReasonCard from "../components/ReasonCard";

const SCENARIOS = [
  { value: "S1", label: "S1 — Normal Week" },
  { value: "S2", label: "S2 — Monsoon Stretch" },
  { value: "S3", label: "S3 — Lean Season" },
];

const POLICY_PAIRS = [
  { value: "rb_mpc", label: "Rule-Based vs MPC", left: "rule_based", right: "mpc" },
  { value: "do_mpc", label: "Diesel-Only vs MPC", left: "diesel_only", right: "mpc" },
  { value: "mpc_pf", label: "MPC vs Perfect Foresight", left: "mpc", right: "perfect_foresight" },
];

const ALL_POLICIES = ["diesel_only", "rule_based", "mpc", "perfect_foresight"];

const POLICY_SECTION_LABELS = {
  diesel_only: "Diesel-Only Baseline",
  rule_based: "Rule-Based Baseline",
  mpc: "Model-Predictive Control",
  perfect_foresight: "Perfect Foresight",
};

const KPI_METRICS = [
  { key: "diesel_l", label: "Diesel", unit: "L", higherIsBetter: false, decimals: 1 },
  { key: "cost_total_inr", label: "Total Cost", unit: "INR", higherIsBetter: false, decimals: 0 },
  { key: "co2_kg", label: "CO2", unit: "kg", higherIsBetter: false, decimals: 1 },
  { key: "renewable_frac", label: "Renewable Frac", unit: "%", higherIsBetter: true, decimals: 1, pct: true },
  { key: "critical_outage_hours", label: "Outage Hours", unit: "h", higherIsBetter: false, decimals: 1 },
  { key: "dg_starts", label: "Diesel Starts", unit: "", higherIsBetter: false, decimals: 0 },
];

/**
 * Analytical workspace: scenario + policy-pair selectors, KPI delta row,
 * two synchronized DispatchTimelines with a shared crosshair + reason
 * panel, and a 4-way KPI reference table toggle. Static JSON only.
 */
export default function Evidence() {
  const [scenario, setScenario] = useState("S2");
  const [pairKey, setPairKey] = useState("rb_mpc");
  const [runs, setRuns] = useState({});
  const [hoveredIndex, setHoveredIndex] = useState(null);
  const [showTable, setShowTable] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setRuns({});
    setHoveredIndex(null);
    setError(null);
    Promise.all(ALL_POLICIES.map((p) => loadRun(scenario, p).then((data) => [p, data])))
      .then((entries) => {
        if (cancelled) return;
        const map = {};
        entries.forEach(([p, data]) => {
          map[p] = data;
        });
        setRuns(map);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [scenario]);

  const pair = POLICY_PAIRS.find((p) => p.value === pairKey);

  if (error) {
    return <div className="p-6 text-body text-status-critical">Failed to load run data: {error}</div>;
  }
  if (!runs[pair.left] || !runs[pair.right]) {
    return (
      <div className="px-6 py-6">
        <div className="flex flex-wrap items-center gap-4 mb-8">
          <SegmentedControl options={SCENARIOS} value={scenario} onChange={setScenario} />
          <SegmentedControl options={POLICY_PAIRS} value={pairKey} onChange={setPairKey} />
        </div>
        <div className="text-body text-text-secondary">Loading…</div>
      </div>
    );
  }

  const leftRun = runs[pair.left];
  const rightRun = runs[pair.right];
  const leftStep = hoveredIndex != null ? leftRun.steps[hoveredIndex] : null;
  const rightStep = hoveredIndex != null ? rightRun.steps[hoveredIndex] : null;

  return (
    <div className="px-6 py-6 flex flex-col gap-8">
      <div className="flex flex-wrap items-center gap-4">
        <SegmentedControl options={SCENARIOS} value={scenario} onChange={setScenario} />
        <SegmentedControl options={POLICY_PAIRS} value={pairKey} onChange={setPairKey} />
      </div>

      <div>
        <SectionLabel className="mb-3">{pair.label} — KPI Comparison</SectionLabel>
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
          {KPI_METRICS.map((m) => (
            <KpiCard
              key={m.key}
              label={m.label}
              value={m.pct ? rightRun.kpi[m.key] * 100 : rightRun.kpi[m.key]}
              unit={m.unit}
              decimals={m.decimals}
              comparisonValue={m.pct ? leftRun.kpi[m.key] * 100 : leftRun.kpi[m.key]}
              higherIsBetter={m.higherIsBetter}
            />
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div>
          <SectionLabel className="mb-2">{POLICY_SECTION_LABELS[pair.left]}</SectionLabel>
          <DispatchTimeline
            steps={leftRun.steps}
            height={280}
            hoveredIndex={hoveredIndex}
            onHoverIndex={setHoveredIndex}
          />
        </div>
        <div>
          <SectionLabel className="mb-2">{POLICY_SECTION_LABELS[pair.right]}</SectionLabel>
          <DispatchTimeline
            steps={rightRun.steps}
            height={280}
            hoveredIndex={hoveredIndex}
            onHoverIndex={setHoveredIndex}
          />
        </div>

        <div className="mt-2 border border-border-subtle rounded p-4 min-h-[96px]">
          {leftStep && rightStep ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <ReasonCard code={leftStep.reason_code} text={leftStep.reason_text} timestamp={leftStep.t} />
              <ReasonCard code={rightStep.reason_code} text={rightStep.reason_text} timestamp={rightStep.t} />
            </div>
          ) : (
            <div className="text-meta text-text-tertiary">Hover a point on either chart to compare reasoning.</div>
          )}
        </div>
      </div>

      <div>
        <button
          type="button"
          onClick={() => setShowTable((v) => !v)}
          className="text-body text-text-secondary hover:text-text-primary border border-border-subtle rounded-sm px-3 py-1.5 transition-colors duration-150 ease-out"
        >
          {showTable ? "Hide" : "Show"} 4-way KPI reference table
        </button>
        {showTable && (
          <div className="mt-4">
            <KpiReferenceTable runs={runs} />
          </div>
        )}
      </div>
    </div>
  );
}
