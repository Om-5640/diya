const POLICIES = ["diesel_only", "rule_based", "mpc", "perfect_foresight"];

const POLICY_LABELS = {
  diesel_only: "Diesel-Only",
  rule_based: "Rule-Based",
  mpc: "MPC",
  perfect_foresight: "Perfect Foresight",
};

const ROWS = [
  { key: "diesel_l", label: "Diesel (L)", decimals: 1 },
  { key: "cost_total_inr", label: "Total Cost (INR)", decimals: 0 },
  { key: "co2_kg", label: "CO2 (kg)", decimals: 1 },
  { key: "renewable_frac", label: "Renewable Frac", decimals: 3 },
  { key: "unserved_critical_kwh", label: "Unserved Critical (kWh)", decimals: 2 },
  { key: "critical_outage_hours", label: "Critical Outage (h)", decimals: 1 },
  { key: "dg_starts", label: "Diesel Starts", decimals: 0 },
  { key: "batt_equivalent_full_cycles", label: "Battery Cycles", decimals: 2 },
  { key: "cost_per_delivered_kwh_inr", label: "Cost / Delivered kWh", decimals: 2 },
  { key: "served_kwh", label: "Served (kWh)", decimals: 1 },
];

function fmt(value, decimals) {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

/**
 * Dense 4-way KPI reference table. The mpc column gets a subtle top+bottom
 * accent-battery border since it's the benchmark column.
 */
export default function KpiReferenceTable({ runs }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-body tabular-nums border-collapse">
        <thead>
          <tr>
            <th className="text-left text-h2 font-semibold uppercase text-text-tertiary pb-2 pr-4">Metric</th>
            {POLICIES.map((p) => (
              <th
                key={p}
                className={`text-right text-h2 font-semibold uppercase text-text-tertiary pb-2 pl-4 whitespace-nowrap ${
                  p === "mpc" ? "border-t border-b border-accent-battery" : ""
                }`}
              >
                {POLICY_LABELS[p]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((row) => (
            <tr key={row.key} className="border-t border-border-subtle">
              <td className="text-left text-text-secondary py-2 pr-4 whitespace-nowrap">{row.label}</td>
              {POLICIES.map((p) => (
                <td
                  key={p}
                  className={`text-right py-2 pl-4 text-text-primary ${
                    p === "mpc" ? "border-l border-r border-accent-battery/40" : ""
                  }`}
                >
                  {fmt(runs[p]?.kpi?.[row.key], row.decimals)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
