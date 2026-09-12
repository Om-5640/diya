import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useEffect } from "react";

const LEGEND_ITEMS = [
  { label: "Solar", color: "var(--accent-solar)" },
  { label: "Wind", color: "var(--accent-wind)" },
  { label: "Diesel", color: "var(--accent-diesel)" },
  { label: "Battery", color: "var(--accent-battery)" },
  { label: "Load", color: "var(--text-primary)" },
  { label: "SOC %", color: "var(--text-tertiary)" },
];

// Base fill opacities establish the visual hierarchy: renewables (solar/wind)
// read as the dominant supply layer, diesel is deliberately capped BELOW
// solar's opacity (in the old dark theme, diesel-orange visually dominated
// even in low-diesel hours -- this fixes that), battery sits similarly
// secondary to diesel.
const BASE_OPACITY = {
  pv_kw: 0.88,
  wind_kw: 0.75,
  dg_kw: 0.5,
  battDischarge: 0.55,
  battCharge: 0.3,
};
const DIMMED_FACTOR = 0.4;

function TimelineLegend() {
  return (
    <div className="flex items-center gap-4 flex-wrap mb-3">
      {LEGEND_ITEMS.map((item) => (
        <div key={item.label} className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-[3px] rounded-sm" style={{ backgroundColor: item.color }} />
          <span className="text-small text-text-secondary">{item.label}</span>
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
 * suppressed so no default tooltip box appears. UNCHANGED from before this
 * redesign -- do not rebuild this mechanism.
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

function dominantSeriesKey(step) {
  if (!step) return null;
  const candidates = {
    pv_kw: step.pv_kw,
    wind_kw: step.wind_kw,
    dg_kw: step.dg_kw,
    battDischarge: Math.max(step.batt_kw, 0),
    battCharge: Math.abs(Math.min(step.batt_kw, 0)),
  };
  let bestKey = null;
  let bestVal = 1e-6;
  for (const [key, val] of Object.entries(candidates)) {
    if (val > bestVal) {
      bestVal = val;
      bestKey = key;
    }
  }
  return bestKey;
}

function TickMarkShape(props) {
  const { cx, cy } = props;
  return <line x1={cx} y1={cy - 4} x2={cx} y2={cy + 4} stroke="var(--status-critical)" strokeWidth={2} />;
}

function DeficitDotShape(props) {
  const { cx, cy } = props;
  return <circle cx={cx} cy={cy} r={3.5} fill="var(--status-critical)" stroke="var(--bg-surface)" strokeWidth={1} />;
}

/**
 * Full-width dispatch timeline: Load is the single strongest stroke (drawn
 * on top), renewables (solar+wind) are the dominant fill layer, diesel and
 * battery are deliberately secondary, SOC is a quiet dashed context line,
 * and critical-deficit hours render as a small x-axis tick + a dot on the
 * load line (not a full-height band). Hovering dims every series except
 * Load and whichever is dominant at that hour.
 *
 * hoveredIndex/onHoverIndex are optional controlled props for cross-chart
 * synchronized hover -- the parent lifts the hovered step index into shared
 * state so two DispatchTimelines can render one logical crosshair. This
 * mechanism is unchanged from before the redesign.
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

  const deficitSteps = steps.filter((s) => s.unserved_critical_kwh > 1e-9);
  const hasDeficits = deficitSteps.length > 0 && !compact;

  let powerDomain = undefined;
  let tickY = 0;
  if (hasDeficits) {
    const allValues = data.flatMap((d) => [d.pv_kw, d.wind_kw, d.dg_kw, d.battDischarge, d.battCharge, d.load]);
    const minValue = Math.min(0, ...allValues);
    tickY = minValue - Math.max(2, Math.abs(minValue) * 0.08 + 1);
    powerDomain = [tickY, "auto"];
  }
  const deficitDots = deficitSteps.map((s) => ({
    t: s.t,
    load: s.load_critical_kw + s.load_essential_kw + s.load_deferrable_kw,
  }));
  const deficitTicks = deficitSteps.map((s) => ({ t: s.t, y: tickY }));

  const dominantKey = hoveredIndex != null ? dominantSeriesKey(steps[hoveredIndex]) : null;
  const opacityFor = (key) => {
    const base = BASE_OPACITY[key];
    if (dominantKey == null || key === dominantKey) return base;
    return base * DIMMED_FACTOR;
  };

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
              domain={powerDomain}
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

            <Area
              yAxisId="power"
              type="monotone"
              dataKey="pv_kw"
              stackId="supply"
              stroke="var(--accent-solar)"
              fill="var(--accent-solar)"
              fillOpacity={opacityFor("pv_kw")}
              strokeOpacity={opacityFor("pv_kw")}
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
              fillOpacity={opacityFor("wind_kw")}
              strokeOpacity={opacityFor("wind_kw")}
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
              fillOpacity={opacityFor("dg_kw")}
              strokeOpacity={opacityFor("dg_kw")}
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
              fillOpacity={opacityFor("battDischarge")}
              strokeOpacity={opacityFor("battDischarge")}
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
              fillOpacity={opacityFor("battCharge")}
              strokeOpacity={opacityFor("battCharge")}
              strokeWidth={1.5}
              isAnimationActive={false}
              activeDot={false}
            />

            <Line
              yAxisId="soc"
              type="monotone"
              dataKey="soc_pct"
              stroke="var(--text-tertiary)"
              strokeDasharray="4 3"
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
              activeDot={false}
            />

            {/* Load: the single strongest stroke, drawn last so it's on top */}
            <Line
              yAxisId="power"
              type="monotone"
              dataKey="load"
              stroke="var(--text-primary)"
              strokeWidth={3}
              dot={false}
              isAnimationActive={false}
              activeDot={false}
            />

            {hasDeficits && (
              <Scatter yAxisId="power" data={deficitTicks} dataKey="y" shape={TickMarkShape} isAnimationActive={false} />
            )}
            {hasDeficits && (
              <Scatter
                yAxisId="power"
                data={deficitDots}
                dataKey="load"
                shape={DeficitDotShape}
                isAnimationActive={false}
              />
            )}

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
