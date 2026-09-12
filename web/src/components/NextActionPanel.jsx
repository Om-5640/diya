function actionHeadline(step) {
  if (step.dg_on === 1) {
    return `RUN DIESEL — ${step.dg_kw.toFixed(1)} kW`;
  }
  if (step.batt_kw < -0.05) {
    return `CHARGE BATTERY — ${Math.abs(step.batt_kw).toFixed(1)} kW`;
  }
  if (step.batt_kw > 0.05) {
    return `DISCHARGE BATTERY — ${step.batt_kw.toFixed(1)} kW`;
  }
  return "HOLD — RENEWABLES COVERING DEMAND";
}

/**
 * Highest visual weight panel in the Operator Live view. Headline first,
 * then reason_text read straight from the step (never hardcoded here).
 */
export default function NextActionPanel({ step }) {
  const isCritical = step.reason_code === "R8_CRITICAL_DEFICIT";
  const panelBorder = isCritical ? "border-status-critical" : "border-accent-battery";
  const reasonBorder = isCritical ? "border-status-critical" : "border-status-good";

  return (
    <div className={`border-2 ${panelBorder} rounded p-6`}>
      <div className="text-h1 font-semibold mb-3 tabular-nums">{actionHeadline(step)}</div>
      <div className={`pl-4 border-l-2 ${reasonBorder} text-body text-text-secondary`}>{step.reason_text}</div>
    </div>
  );
}
