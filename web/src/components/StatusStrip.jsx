export default function StatusStrip({ step }) {
  const criticalOK = step.unserved_critical_kwh < 1e-6;
  const socPct = Math.max(0, Math.min(100, step.soc_pct));

  return (
    <div className="py-6 flex items-center gap-10 flex-wrap">
      <div className="flex flex-col gap-2 min-w-[180px]">
        <span className="text-h2 font-semibold uppercase text-text-tertiary">Battery SOC</span>
        <span key={step.soc_pct} className="text-display font-semibold tabular-nums kpi-fade">
          {step.soc_pct.toFixed(0)}%
        </span>
        <div className="w-full h-1.5 bg-bg-panel-raised rounded-sm overflow-hidden">
          <div className="h-full bg-accent-battery" style={{ width: `${socPct}%` }} />
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-h2 font-semibold uppercase text-text-tertiary">Critical Load</span>
        <span className={`text-h1 font-semibold ${criticalOK ? "text-status-good" : "text-status-critical"}`}>
          {criticalOK ? "PROTECTED" : "AT RISK"}
        </span>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-h2 font-semibold uppercase text-text-tertiary">Diesel</span>
        <span
          className={`inline-flex items-center self-start px-2 py-1 rounded-sm text-body font-semibold ${
            step.dg_on === 1 ? "bg-accent-diesel/20 text-accent-diesel" : "bg-bg-panel-raised text-text-secondary"
          }`}
        >
          {step.dg_on === 1 ? "ON" : "OFF"}
        </span>
      </div>
    </div>
  );
}
