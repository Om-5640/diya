import { useCallback, useEffect, useRef, useState } from "react";
import { loadRun } from "../lib/loadRun";
import StatusStrip from "../components/StatusStrip";
import SectionLabel from "../components/SectionLabel";
import VillageVisual from "../components/VillageVisual";
import NextActionPanel from "../components/NextActionPanel";
import Next72Strip from "../components/Next72Strip";

const KHAVDA_LAT = 23.8443;
const KHAVDA_LON = 69.7317;
const CLIENT_TIMEOUT_MS = 5000;
const DEFAULT_SOC_PCT = 60;

async function fetchSolveLive(socPct) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CLIENT_TIMEOUT_MS);
  try {
    const res = await fetch("/api/solve_live", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: KHAVDA_LAT, lon: KHAVDA_LON, soc_pct: socPct }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`solve_live HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Single full-bleed vertical narrative. On mount, attempts a live 72h
 * /api/solve_live plan for Khavda; on any failure (network down, non-200,
 * or client-side 5s timeout) it falls back to the static S2_mpc.json data
 * from Phase 1B with a small inline notice -- the view never blocks or
 * throws either way. "Re-plan now" re-fires the same request on demand.
 */
export default function OperatorLive() {
  const [staticRun, setStaticRun] = useState(null);
  const [liveRun, setLiveRun] = useState(null);
  const [isLoadingLive, setIsLoadingLive] = useState(true);
  const [liveFailed, setLiveFailed] = useState(false);
  const [hasSettledOnce, setHasSettledOnce] = useState(false);
  const [socPct, setSocPct] = useState(DEFAULT_SOC_PCT);
  const requestSeq = useRef(0);

  useEffect(() => {
    let cancelled = false;
    loadRun("S2", "mpc")
      .then((data) => {
        if (!cancelled) setStaticRun(data);
      })
      .catch(() => {
        // static fallback failing too is a hard error case handled below by
        // the "nothing to show yet" branch -- nothing to set here
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const requestLive = useCallback((soc) => {
    const seq = ++requestSeq.current;
    setIsLoadingLive(true);
    fetchSolveLive(soc)
      .then((data) => {
        if (seq !== requestSeq.current) return;
        setLiveRun(data);
        setLiveFailed(false);
      })
      .catch(() => {
        if (seq !== requestSeq.current) return;
        setLiveRun(null);
        setLiveFailed(true);
      })
      .finally(() => {
        if (seq !== requestSeq.current) return;
        setIsLoadingLive(false);
        setHasSettledOnce(true);
      });
  }, []);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => requestLive(DEFAULT_SOC_PCT), []);

  const activeRun = liveRun ?? staticRun;

  // Full-page loading text only gates the very first mount-time attempt --
  // "Re-plan now" clicks afterward keep the current view visible and show
  // their own brief inline loading state on the button instead.
  if (!hasSettledOnce) {
    return <div className="p-6 text-body text-text-secondary">Fetching live forecast…</div>;
  }
  if (!activeRun) {
    return <div className="p-6 text-body text-text-secondary">Loading…</div>;
  }

  const currentIndex = 0;
  const step = activeRun.steps[currentIndex];

  const solarActive = step.pv_kw > 0.1;
  const windActive = step.wind_kw > 0.1;
  const dieselActive = step.dg_on === 1;
  const batteryPct = step.soc_pct;
  const criticalOK = step.unserved_critical_kwh < 1e-6;

  return (
    <div className="px-6">
      <div className="py-4 flex flex-wrap items-center justify-between gap-4 border-b border-border-subtle">
        <div className="flex items-center gap-4 flex-wrap">
          <label className="flex items-center gap-3 text-body text-text-secondary">
            SOC%
            <input
              type="range"
              min="20"
              max="95"
              value={socPct}
              onChange={(e) => setSocPct(Number(e.target.value))}
              className="w-32 accent-[var(--accent-battery)]"
            />
            <span className="tabular-nums text-text-primary w-10">{socPct}%</span>
          </label>
          <button
            type="button"
            onClick={() => requestLive(socPct)}
            disabled={isLoadingLive}
            className="text-body text-text-primary border border-border-subtle hover:border-border-strong rounded-sm px-3 py-1.5 transition-colors duration-150 ease-out disabled:opacity-60"
          >
            {isLoadingLive ? "Re-planning…" : "Re-plan now"}
          </button>
        </div>
        {liveFailed && (
          <span className="text-meta text-status-warn border border-status-warn/40 bg-status-warn/10 rounded-sm px-2.5 py-1">
            Live forecast unavailable — showing cached scenario data
          </span>
        )}
        {!liveFailed && liveRun && (
          <span className="text-meta text-text-tertiary">
            Live plan · forecast: {liveRun.provenance?.forecast_method ?? "unknown"}
          </span>
        )}
      </div>

      <div className="border-b border-border-subtle">
        <StatusStrip step={step} />
      </div>

      <div className="py-8 border-b border-border-subtle">
        <SectionLabel className="mb-4">System State</SectionLabel>
        <VillageVisual
          solarActive={solarActive}
          windActive={windActive}
          dieselActive={dieselActive}
          batteryPct={batteryPct}
          criticalOK={criticalOK}
        />
      </div>

      <div className="py-8 border-b border-border-subtle">
        <SectionLabel className="mb-4">Next Action</SectionLabel>
        <NextActionPanel step={step} />
      </div>

      <div className="py-8">
        <Next72Strip steps={activeRun.steps} currentIndex={currentIndex} />
      </div>
    </div>
  );
}
