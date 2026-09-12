/**
 * Shared KPI formatting and higherIsBetter-aware delta logic. KpiCard reads
 * these; do not reimplement the good/bad decision anywhere else.
 */

export function formatNumber(value, decimals = 1) {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/**
 * delta = value - comparisonValue. isGood is true when the change is an
 * improvement given higherIsBetter (e.g. cost/diesel_l: lower is better;
 * renewable_frac: higher is better).
 */
export function computeDelta(value, comparisonValue, higherIsBetter) {
  if (value == null || comparisonValue == null) return null;
  const delta = value - comparisonValue;
  const isGood = higherIsBetter ? delta >= 0 : delta <= 0;
  return { delta, isGood };
}
