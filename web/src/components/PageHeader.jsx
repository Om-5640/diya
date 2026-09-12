/**
 * PageHeader(eyebrow?, title, meta?, right?): the one header shape shared
 * by all 3 routes. `eyebrow` is the small brand wordmark line (Overview
 * only); `right` holds route-specific badges/indicators.
 */
export default function PageHeader({ eyebrow, title, meta, right }) {
  return (
    <header className="flex items-start justify-between gap-6 flex-wrap px-8 py-6 border-b border-border-subtle bg-bg-surface">
      <div className="min-w-0">
        {eyebrow && <div className="text-label uppercase text-brand mb-1.5">{eyebrow}</div>}
        <h1 className="text-title text-text-primary">{title}</h1>
        {meta && <p className="text-small text-text-tertiary mt-1.5">{meta}</p>}
      </div>
      {right && <div className="flex items-center gap-3 flex-wrap shrink-0">{right}</div>}
    </header>
  );
}
