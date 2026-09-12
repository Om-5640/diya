import SectionLabel from "./SectionLabel";
import DispatchTimeline from "./DispatchTimeline";

/**
 * Overview's intentionally-reduced-detail forecast: a compact mini-chart
 * over the next `hours` steps, ending in a link to the full Live Dispatch
 * page. Never shows the full 168-hour view -- that's Live Dispatch's job.
 */
export default function ShortForecast({ steps, currentIndex, hours = 72, onOpenFull }) {
  const window = steps.slice(currentIndex, currentIndex + hours);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <SectionLabel>Short Forecast</SectionLabel>
        <button
          type="button"
          onClick={onOpenFull}
          className="text-body text-brand font-medium hover:underline underline-offset-2"
        >
          Open full dispatch →
        </button>
      </div>
      <DispatchTimeline steps={window} height={140} compact />
    </div>
  );
}
