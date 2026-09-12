/**
 * ReasonCard(code, text, timestamp). Renders the reason_text exactly as
 * read from the run JSON -- never hardcode reason copy here.
 * R8_CRITICAL_DEFICIT is the one code rendered as a distinct warning state.
 */
export default function ReasonCard({ code, text, timestamp, title }) {
  const isCritical = code === "R8_CRITICAL_DEFICIT";

  return (
    <div
      className={`rounded-md p-4 border ${
        isCritical ? "border-status-critical/40 bg-status-critical/5" : "border-border-subtle bg-bg-surface"
      }`}
    >
      {title && <div className="text-small font-medium text-text-tertiary mb-2">{title}</div>}
      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
        {isCritical && (
          <span aria-hidden="true" className="text-status-critical">
            ⚠
          </span>
        )}
        <span
          className={`text-small font-medium rounded-md px-1.5 py-0.5 tabular-nums border ${
            isCritical
              ? "bg-status-critical/10 text-status-critical border-status-critical/30"
              : "bg-bg-surface-sunken text-text-secondary border-border-subtle"
          }`}
        >
          {code}
        </span>
        {timestamp && <span className="text-small text-text-tertiary">{timestamp}</span>}
      </div>
      <div className={`text-body ${isCritical ? "text-text-primary" : "text-text-secondary"}`}>{text}</div>
    </div>
  );
}
