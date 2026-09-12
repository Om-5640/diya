import { computeDelta, formatNumber } from "../lib/kpi";

const SIZE_CLASSES = {
  hero: { value: "text-metric", label: "text-label uppercase" },
  supporting: { value: "text-title", label: "text-small" },
  compact: { value: "text-body font-medium", label: "text-small" },
};

/**
 * Metric(label, value, unit, comparisonValue?, higherIsBetter, size?): the
 * one shared value primitive used for Overview's dominant number, Evidence's
 * hero impact numbers, and supporting/compact rows everywhere else. Delta
 * math is untouched -- reuses the exact computeDelta/formatNumber used
 * before this redesign.
 */
export default function Metric({
  label,
  value,
  unit,
  decimals = 1,
  comparisonValue,
  higherIsBetter,
  size = "supporting",
  emphasize = false,
}) {
  const delta = comparisonValue != null ? computeDelta(value, comparisonValue, higherIsBetter) : null;
  const cls = SIZE_CLASSES[size];

  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      <div className={`${cls.label} text-text-tertiary truncate`}>{label}</div>
      <div
        key={value}
        className={`${cls.value} tabular-nums kpi-fade truncate ${emphasize ? "text-brand" : "text-text-primary"}`}
      >
        {formatNumber(value, decimals)}
        {unit ? <span className="text-body font-normal text-text-secondary ml-1">{unit}</span> : null}
      </div>
      {delta ? (
        <span
          className={`inline-flex items-center gap-1 text-small font-medium tabular-nums self-start ${
            delta.isGood ? "text-status-good" : "text-status-critical"
          }`}
        >
          <span aria-hidden>{delta.delta >= 0 ? "▲" : "▼"}</span>
          <span>{formatNumber(Math.abs(delta.delta), decimals)}</span>
          {unit ? <span>{unit}</span> : null}
        </span>
      ) : null}
    </div>
  );
}
