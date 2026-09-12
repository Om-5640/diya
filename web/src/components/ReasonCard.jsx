/**
 * ReasonCard(code, text, timestamp). Renders the reason_text exactly as
 * read from the run JSON -- never hardcode reason copy here.
 * R8_CRITICAL_DEFICIT is the one code rendered as a distinct warning state.
 */
export default function ReasonCard({ code, text, timestamp }) {
  const isCritical = code === "R8_CRITICAL_DEFICIT";

  return (
    <div
      className={`rounded p-4 border ${
        isCritical ? "border-status-critical bg-status-critical/10" : "border-border-subtle bg-bg-panel"
      }`}
    >
      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
        {isCritical && (
          <span aria-hidden="true" className="text-status-critical">
            ⚠
          </span>
        )}
        <span
          className={`text-meta font-semibold rounded-sm px-1.5 py-0.5 tabular-nums ${
            isCritical ? "bg-status-critical text-bg-base" : "bg-bg-panel-raised text-text-secondary"
          }`}
        >
          {code}
        </span>
        {timestamp && <span className="text-meta text-text-tertiary">{timestamp}</span>}
      </div>
      <div className={`text-body ${isCritical ? "text-text-primary" : "text-text-secondary"}`}>{text}</div>
    </div>
  );
}
