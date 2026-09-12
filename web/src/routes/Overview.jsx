import PageHeader from "../components/PageHeader";
import SectionLabel from "../components/SectionLabel";
import StatusBadge from "../components/StatusBadge";
import Metric from "../components/Metric";
import VillageVisual from "../components/VillageVisual";
import DecisionCard from "../components/DecisionCard";
import ShortForecast from "../components/ShortForecast";

function buildHeroStatement(step) {
  const criticalOK = step.unserved_critical_kwh < 1e-6;
  const dieselOn = step.dg_on === 1;
  const soc = Math.round(step.soc_pct);
  const healthPart = criticalOK ? "The health centre is protected" : "The health centre's supply is at risk";
  const dieselPart = dieselOn ? "diesel is running" : "diesel is off";
  return `${healthPart} — ${dieselPart} and the battery is at ${soc}%.`;
}

function actionHeadline(step) {
  if (step.dg_on === 1) return `Run diesel — ${step.dg_kw.toFixed(1)} kW`;
  if (step.batt_kw < -0.05) return `Charge battery — ${Math.abs(step.batt_kw).toFixed(1)} kW`;
  if (step.batt_kw > 0.05) return `Discharge battery — ${step.batt_kw.toFixed(1)} kW`;
  return "Hold — renewables covering demand";
}

/**
 * Overview: a homepage, not a dashboard. Strict top-to-bottom order --
 * state -> health -> action -> why -> short forecast -- with nothing above
 * Next Action competing for attention. `liveDispatch` (the useLiveDispatch()
 * result) is lifted to App.jsx and shared with Live Dispatch as props, so
 * navigating between the two routes never re-fetches.
 */
export default function Overview({ onNavigate, liveDispatch }) {
  const { activeRun, liveRun, isLoadingLive, liveFailed, hasSettledOnce, socPct, setSocPct, requestLive } =
    liveDispatch;

  if (!hasSettledOnce) {
    return <div className="p-8 text-body text-text-secondary">Fetching live forecast…</div>;
  }
  if (!activeRun) {
    return <div className="p-8 text-body text-text-secondary">Loading…</div>;
  }

  const currentIndex = 0;
  const step = activeRun.steps[currentIndex];

  const solarActive = step.pv_kw > 0.1;
  const windActive = step.wind_kw > 0.1;
  const dieselActive = step.dg_on === 1;
  const criticalOK = step.unserved_critical_kwh < 1e-6;

  const renewableShare =
    ((step.pv_kw + step.wind_kw) / Math.max(1e-9, step.pv_kw + step.wind_kw + step.dg_kw)) * 100;
  const totalLoad = step.load_critical_kw + step.load_essential_kw + step.load_deferrable_kw;

  const scenarioLabel = liveRun ? "Live Forecast" : "S2 — Monsoon Stretch";

  return (
    <div>
      <PageHeader
        eyebrow="DIYA"
        title="Khavda, Kutch"
        meta="180 households · 1 health centre"
        right={
          <>
            <StatusBadge variant="neutral">{scenarioLabel}</StatusBadge>
            <StatusBadge variant="good">
              <span className="w-1.5 h-1.5 rounded-full bg-status-good inline-block" aria-hidden="true" />
              Offline-ready
            </StatusBadge>
          </>
        }
      />

      <div className="px-8 py-4 border-b border-border-subtle flex items-center gap-4 flex-wrap">
        <label className="flex items-center gap-3 text-body text-text-secondary">
          SOC%
          <input
            type="range"
            min="20"
            max="95"
            value={socPct}
            onChange={(e) => setSocPct(Number(e.target.value))}
            className="w-32 accent-brand"
          />
          <span className="tabular-nums text-text-primary w-10">{socPct}%</span>
        </label>
        <button
          type="button"
          onClick={() => requestLive(socPct)}
          disabled={isLoadingLive}
          className="text-body text-text-primary border border-border-subtle hover:border-border-strong rounded-md px-3 py-1.5 transition-colors duration-150 ease-out disabled:opacity-60"
        >
          {isLoadingLive ? "Re-planning…" : "Re-plan now"}
        </button>
        {liveFailed && (
          <StatusBadge variant="warn">Live forecast unavailable — showing cached scenario data</StatusBadge>
        )}
      </div>

      <div className="px-8 py-10 border-b border-border-subtle">
        <p className="font-serif text-statement text-text-primary max-w-3xl mb-8">{buildHeroStatement(step)}</p>
        <VillageVisual
          solarActive={solarActive}
          windActive={windActive}
          dieselActive={dieselActive}
          batteryPct={step.soc_pct}
          criticalOK={criticalOK}
        />
      </div>

      <div className="px-8 py-8 border-b border-border-subtle">
        <div className="flex flex-wrap items-start gap-12">
          <Metric label="Renewable share" value={renewableShare} unit="%" decimals={0} size="hero" emphasize />
          <span className="text-small text-text-tertiary -mt-1 self-end pb-2">of current supply</span>
          <div className="flex flex-wrap gap-10 ml-auto">
            <Metric label="Battery" value={step.soc_pct} unit="%" decimals={0} size="compact" />
            <Metric label="Diesel" value={step.dg_kw} unit="kW" decimals={1} size="compact" />
            <Metric label="Total load" value={totalLoad} unit="kW" decimals={1} size="compact" />
            <div className="flex flex-col gap-1.5">
              <div className="text-small text-text-tertiary">Critical load</div>
              <StatusBadge variant={criticalOK ? "good" : "critical"}>
                {criticalOK ? "Protected" : "At risk"}
              </StatusBadge>
            </div>
          </div>
        </div>
      </div>

      <div className="px-8 py-8 border-b border-border-subtle">
        <SectionLabel className="mb-4">Next Action</SectionLabel>
        <DecisionCard headline={actionHeadline(step)} reasonText={step.reason_text} isCritical={!criticalOK}>
          <div className="flex flex-wrap gap-10">
            <Metric label="Battery after action" value={step.soc_pct} unit="%" decimals={0} size="compact" />
            <Metric label="Diesel" value={dieselActive ? step.dg_kw : 0} unit="kW" decimals={1} size="compact" />
          </div>
        </DecisionCard>
      </div>

      <div className="px-8 py-8">
        <ShortForecast steps={activeRun.steps} currentIndex={currentIndex} onOpenFull={() => onNavigate("live")} />
      </div>
    </div>
  );
}
