import { computeDelta, formatNumber } from "../lib/kpi";

/**
 * KpiCard(label, value, unit, comparisonValue?, higherIsBetter): renders a
 * green/red delta badge with an explicit arrow when comparisonValue is given.
 */
export default function KpiCard({ label, value, unit, decimals = 1, comparisonValue, higherIsBetter }) {
  const delta = comparisonValue != null ? computeDelta(value, comparisonValue, higherIsBetter) : null;

  return (
    <div className="bg-bg-panel border border-border-subtle rounded p-6 flex flex-col gap-2 min-w-0">
      <div className="text-h2 font-semibold uppercase text-text-tertiary truncate">{label}</div>
      <div key={value} className="text-display font-semibold tabular-nums kpi-fade truncate">
        {formatNumber(value, decimals)}
        {unit ? <span className="text-body font-normal text-text-secondary ml-1">{unit}</span> : null}
      </div>
      {delta ? (
        <span
          className={`inline-flex items-center gap-1 text-meta font-medium rounded-sm px-1.5 py-0.5 self-start tabular-nums ${
            delta.isGood ? "text-status-good bg-status-good/10" : "text-status-critical bg-status-critical/10"
          }`}
        >
          <span aria-hidden>{delta.delta >= 0 ? "▲" : "▼"}</span>
          <span>{formatNumber(Math.abs(delta.delta), decimals)}</span>
          {unit ? <span>{unit}</span> : null}
        </span>
      ) : (
        <span className="text-meta text-text-tertiary">&nbsp;</span>
      )}
    </div>
  );
}
