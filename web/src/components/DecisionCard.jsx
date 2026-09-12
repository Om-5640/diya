/**
 * DecisionCard(headline, reasonText, isCritical?, children?): the
 * highest-visual-weight panel pattern, used for Next Action on Overview.
 * One continuous panel -- headline, then reason_text read straight from
 * the step (never invented here), then an optional quiet secondary row.
 */
export default function DecisionCard({ headline, reasonText, isCritical = false, children }) {
  return (
    <div
      className={`border-l-4 ${isCritical ? "border-l-status-critical" : "border-l-brand"} border border-border-strong bg-bg-surface rounded-md p-8`}
    >
      <div className="text-title text-text-primary mb-3 tabular-nums">{headline}</div>
      <div className="text-body text-text-secondary">{reasonText}</div>
      {children && <div className="mt-5 pt-5 border-t border-border-subtle">{children}</div>}
    </div>
  );
}
