import { useEffect } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const LEGEND_ITEMS = [
  { label: "Solar", color: "var(--accent-solar)" },
  { label: "Wind", color: "var(--accent-wind)" },
  { label: "Diesel", color: "var(--accent-diesel)" },
  { label: "Battery", color: "var(--accent-battery)" },
  { label: "Load", color: "#FFFFFF" },
  { label: "SOC %", color: "var(--text-secondary)" },
];

function TimelineLegend() {
  return (
    <div className="flex items-center gap-4 flex-wrap mb-2">
      {LEGEND_ITEMS.map((item) => (
        <div key={item.label} className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-[3px] rounded-sm" style={{ backgroundColor: item.color }} />
          <span className="text-meta text-text-secondary">{item.label}</span>
        </div>
      ))}
    </div>
  );
}

function formatTick(t) {
  const d = new Date(t);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { weekday: "short", hour: "2-digit" });
}

/**
 * Bridges Recharts' own hover-tracking (only reliably observable via the
 * Tooltip content render-prop, which recharts re-invokes on every active/
 * label change) into the lifted `onHoverIndex` state that synchronizes two
 * separate chart instances. Renders nothing -- Tooltip content is otherwise
 * suppressed so no default tooltip box appears.
 */
function HoverSync({ active, label, onHoverIndex, steps }) {
  useEffect(() => {
    if (!onHoverIndex) return;
    if (active && label != null) {
      const idx = steps.findIndex((s) => s.t === label);
      onHoverIndex(idx >= 0 ? idx : null);
    } else {
      onHoverIndex(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, label]);
  return null;
}

function computeUnservedBands(steps) {
  const bands = [];
  let startT = null;
  steps.forEach((s, i) => {
    const bad = s.unserved_critical_kwh > 1e-9;
    if (bad && startT === null) startT = s.t;
    if (!bad && startT !== null) {
      bands.push([startT, steps[i - 1].t]);
      startT = null;
    }
  });
  if (startT !== null) bands.push([startT, steps[steps.length - 1].t]);
  return bands;
}

/**
 * Full-width dispatch timeline (stacked supply areas + load line + SOC% on
 * a second axis + unserved-critical bands). Used both as the full Evidence
 * chart and (compact=true) as the Operator Live "next 72h" mini-chart.
 *
 * hoveredIndex/onHoverIndex are optional controlled props for cross-chart
 * synchronized hover: the parent lifts the hovered step index into shared
 * state so two DispatchTimelines can render one logical crosshair.
 */
export default function DispatchTimeline({ steps, height = 280, compact = false, hoveredIndex = null, onHoverIndex }) {
  const data = steps.map((s) => ({
    t: s.t,
    pv_kw: s.pv_kw,
    wind_kw: s.wind_kw,
    dg_kw: s.dg_kw,
    battDischarge: Math.max(s.batt_kw, 0),
    battCharge: Math.min(s.batt_kw, 0),
    load: s.load_critical_kw + s.load_essential_kw + s.load_deferrable_kw,
    soc_pct: s.soc_pct,
  }));

  const bands = compact ? [] : computeUnservedBands(steps);

  const crosshairT = hoveredIndex != null && steps[hoveredIndex] ? steps[hoveredIndex].t : null;

  return (
    <div>
      {!compact && <TimelineLegend />}
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 4, right: compact ? 4 : 12, left: compact ? 4 : 0, bottom: 0 }}>
            <CartesianGrid stroke="var(--border-subtle)" vertical={false} />
            <XAxis
              dataKey="t"
              tickFormatter={formatTick}
              tick={compact ? false : { fontSize: 12, fill: "var(--text-tertiary)" }}
              axisLine={{ stroke: "var(--border-subtle)" }}
              tickLine={false}
              minTickGap={48}
              hide={compact}
            />
            <YAxis
              yAxisId="power"
              tick={compact ? false : { fontSize: 12, fill: "var(--text-tertiary)" }}
              axisLine={false}
              tickLine={false}
              width={compact ? 0 : 36}
              hide={compact}
            />
            <YAxis
              yAxisId="soc"
              orientation="right"
              domain={[0, 100]}
              tick={compact ? false : { fontSize: 12, fill: "var(--text-tertiary)" }}
              axisLine={false}
              tickLine={false}
              width={compact ? 0 : 34}
              hide={compact}
            />

            {bands.map(([x1, x2], i) => (
              <ReferenceArea
                key={`band-${i}`}
                x1={x1}
                x2={x2}
                yAxisId="power"
                fill="var(--status-critical)"
                fillOpacity={0.12}
                stroke="none"
              />
            ))}

            <Area
              yAxisId="power"
              type="monotone"
              dataKey="pv_kw"
              stackId="supply"
              stroke="var(--accent-solar)"
              fill="var(--accent-solar)"
              fillOpacity={0.85}
              strokeWidth={2}
              isAnimationActive={false}
              activeDot={false}
            />
            <Area
              yAxisId="power"
              type="monotone"
              dataKey="wind_kw"
              stackId="supply"
              stroke="var(--accent-wind)"
              fill="var(--accent-wind)"
              fillOpacity={0.72}
              strokeWidth={2}
              isAnimationActive={false}
              activeDot={false}
            />
            <Area
              yAxisId="power"
              type="monotone"
              dataKey="dg_kw"
              stackId="supply"
              stroke="var(--accent-diesel)"
              fill="var(--accent-diesel)"
              fillOpacity={0.6}
              strokeWidth={2}
              isAnimationActive={false}
              activeDot={false}
            />
            <Area
              yAxisId="power"
              type="monotone"
              dataKey="battDischarge"
              stackId="supply"
              stroke="var(--accent-battery)"
              fill="var(--accent-battery)"
              fillOpacity={0.5}
              strokeWidth={2}
              isAnimationActive={false}
              activeDot={false}
            />
            <Area
              yAxisId="power"
              type="monotone"
              dataKey="battCharge"
              stroke="var(--accent-battery)"
              fill="var(--accent-battery)"
              fillOpacity={0.3}
              strokeWidth={1.5}
              isAnimationActive={false}
              activeDot={false}
            />

            <Line
              yAxisId="power"
              type="monotone"
              dataKey="load"
              stroke="#FFFFFF"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              activeDot={false}
            />
            <Line
              yAxisId="soc"
              type="monotone"
              dataKey="soc_pct"
              stroke="var(--text-secondary)"
              strokeDasharray="4 3"
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
              activeDot={false}
            />

            <Tooltip
              content={(props) => <HoverSync {...props} onHoverIndex={onHoverIndex} steps={steps} />}
              cursor={false}
            />

            {crosshairT != null && (
              <ReferenceLine x={crosshairT} yAxisId="power" stroke="var(--border-strong)" strokeWidth={1} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
