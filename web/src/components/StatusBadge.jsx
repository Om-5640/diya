const VARIANTS = {
  good: "text-status-good bg-status-good/10 border-status-good/30",
  warn: "text-status-warn bg-status-warn/10 border-status-warn/30",
  critical: "text-status-critical bg-status-critical/10 border-status-critical/30",
  neutral: "text-text-secondary bg-bg-surface-sunken border-border-subtle",
};

/** StatusBadge(variant, children): good/warn/critical/neutral pill-free tag. */
export default function StatusBadge({ variant = "neutral", children, className = "" }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-small font-medium border rounded-md px-2 py-1 ${VARIANTS[variant]} ${className}`}
    >
      {children}
    </span>
  );
}
