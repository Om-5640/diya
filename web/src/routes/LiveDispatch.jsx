import { useMemo, useState } from "react";
import PageHeader from "../components/PageHeader";
import SectionLabel from "../components/SectionLabel";
import StatusBadge from "../components/StatusBadge";
import Metric from "../components/Metric";
import ScenarioSegmentedControl from "../components/ScenarioSegmentedControl";
import DispatchTimeline from "../components/DispatchTimeline";
import ReasonCard from "../components/ReasonCard";

const RANGE_OPTIONS = [
  { value: "72h", label: "72h" },
  { value: "full", label: "Full week" },
];

function fmt(value, decimals = 1) {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

/**
 * Live Dispatch: the full detailed timeline for the same live-or-static run
 * Overview shows, plus a Selected Hour breakdown. Defaults to a 72h window
 * with a "Full week" toggle; hover/reason-lookup works correctly in either
 * range since it reads the currently-visible slice.
 */
export default function LiveDispatch({ liveDispatch }) {
  const { activeRun, liveRun, hasSettledOnce } = liveDispatch;
  const [range, setRange] = useState("72h");
  const [hoveredIndex, setHoveredIndex] = useState(null);

  const visibleSteps = useMemo(() => {
    if (!activeRun) return [];
    return range === "72h" ? activeRun.steps.slice(0, 72) : activeRun.steps;
  }, [activeRun, range]);

  if (!hasSettledOnce) {
    return <div className="p-8 text-body text-text-secondary">Fetching live forecast…</div>;
  }
  if (!activeRun) {
    return <div className="p-8 text-body text-text-secondary">Loading…</div>;
  }

  const current = activeRun.steps[0];
  const criticalOK = current.unserved_critical_kwh < 1e-6;
  const scenarioLabel = liveRun ? "Live Forecast" : "S2 — Monsoon Stretch";
  const selected = hoveredIndex != null ? visibleSteps[hoveredIndex] : null;

  return (
    <div>
      <PageHeader
        title="Live Dispatch"
        meta={`${activeRun.steps.length}-hour operational view`}
        right={
          <>
            <StatusBadge variant="neutral">{scenarioLabel}</StatusBadge>
            <StatusBadge variant={criticalOK ? "good" : "critical"}>
              {criticalOK ? "Critical load protected" : "Critical load at risk"}
            </StatusBadge>
          </>
        }
      />

      <div className="px-8 py-5 border-b border-border-subtle flex flex-wrap items-center gap-10">
        <Metric label="Battery" value={current.soc_pct} unit="%" decimals={0} size="compact" />
        <Metric label="Diesel" value={current.dg_kw} unit="kW" decimals={1} size="compact" />
        <Metric
          label="Total load"
          value={current.load_critical_kw + current.load_essential_kw + current.load_deferrable_kw}
          unit="kW"
          decimals={1}
          size="compact"
        />
      </div>

      <div className="px-8 py-8 border-b border-border-subtle">
        <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
          <SectionLabel>Full Dispatch Timeline</SectionLabel>
          <ScenarioSegmentedControl options={RANGE_OPTIONS} value={range} onChange={setRange} />
        </div>
        <DispatchTimeline
          steps={visibleSteps}
          height={340}
          hoveredIndex={hoveredIndex}
          onHoverIndex={setHoveredIndex}
        />
      </div>

      <div className="px-8 py-8">
        <SectionLabel className="mb-4">Selected Hour</SectionLabel>
        {!selected ? (
          <div className="border border-border-subtle rounded-md p-6 text-body text-text-tertiary">
            Hover a point on the chart to inspect that hour.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="border border-border-subtle rounded-md p-5 bg-bg-surface">
              <div className="text-small font-medium text-text-tertiary mb-3">What happened — {selected.t}</div>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-body tabular-nums">
                <dt className="text-text-secondary">Solar</dt>
                <dd className="text-right text-text-primary">{fmt(selected.pv_kw)} kW</dd>
                <dt className="text-text-secondary">Wind</dt>
                <dd className="text-right text-text-primary">{fmt(selected.wind_kw)} kW</dd>
                <dt className="text-text-secondary">Diesel</dt>
                <dd className="text-right text-text-primary">{fmt(selected.dg_kw)} kW</dd>
                <dt className="text-text-secondary">Battery</dt>
                <dd className="text-right text-text-primary">{fmt(selected.batt_kw)} kW</dd>
                <dt className="text-text-secondary">SOC</dt>
                <dd className="text-right text-text-primary">{fmt(selected.soc_pct, 0)}%</dd>
                <dt className="text-text-secondary">Load (total)</dt>
                <dd className="text-right text-text-primary">
                  {fmt(selected.load_critical_kw + selected.load_essential_kw + selected.load_deferrable_kw)} kW
                </dd>
                <dt className="text-text-secondary">Fuel</dt>
                <dd className="text-right text-text-primary">{fmt(selected.fuel_l, 2)} L</dd>
              </dl>
            </div>
            <ReasonCard
              title="Why DIYA chose this"
              code={selected.reason_code}
              text={selected.reason_text}
              timestamp={selected.t}
            />
          </div>
        )}
      </div>
    </div>
  );
}
