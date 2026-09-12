import { useEffect, useState } from "react";
import { loadRun } from "../lib/loadRun";
import StatusStrip from "../components/StatusStrip";
import SectionLabel from "../components/SectionLabel";
import VillageVisual from "../components/VillageVisual";
import NextActionPanel from "../components/NextActionPanel";
import Next72Strip from "../components/Next72Strip";

/**
 * Single full-bleed vertical narrative fed from S2_mpc.json (the hero
 * scenario). Static JSON only -- no backend calls in this phase.
 */
export default function OperatorLive() {
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    loadRun("S2", "mpc")
      .then((data) => {
        if (!cancelled) setRun(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return <div className="p-6 text-body text-status-critical">Failed to load run data: {error}</div>;
  }
  if (!run) {
    return <div className="p-6 text-body text-text-secondary">Loading…</div>;
  }

  // The "current hour" is the step closest to now if in range, else step 0.
  // This run is fixed JSON with no live endpoint yet, so it always resolves
  // to step 0 -- that's expected for this phase.
  const currentIndex = 0;
  const step = run.steps[currentIndex];

  const solarActive = step.pv_kw > 0.1;
  const windActive = step.wind_kw > 0.1;
  const dieselActive = step.dg_on === 1;
  const batteryPct = step.soc_pct;
  const criticalOK = step.unserved_critical_kwh < 1e-6;

  return (
    <div className="px-6">
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
        <Next72Strip steps={run.steps} currentIndex={currentIndex} />
      </div>
    </div>
  );
}
