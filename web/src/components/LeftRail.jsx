function OverviewIcon(props) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <path d="M3 11l9-7 9 7" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M5 10v9h14v-9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function LiveIcon(props) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <path d="M3 12h4l2 7 4-14 2 7h6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function EvidenceIcon(props) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <rect x="4" y="11" width="4" height="9" />
      <rect x="10" y="7" width="4" height="13" />
      <rect x="16" y="4" width="4" height="16" />
    </svg>
  );
}

const NAV_ITEMS = [
  { key: "overview", group: "Understand", label: "Overview", Icon: OverviewIcon },
  { key: "live", group: "Operate", label: "Live Dispatch", Icon: LiveIcon },
  { key: "evidence", group: "Prove", label: "Evidence", Icon: EvidenceIcon },
];

function NavItem({ item, active, onChange, iconOnly }) {
  const isActive = active === item.key;
  return (
    <button
      type="button"
      onClick={() => onChange(item.key)}
      title={item.label}
      className={`group relative w-full flex items-center gap-3 px-4 py-2.5 border-l-2 transition-colors duration-150 ease-out ${
        isActive ? "border-l-brand text-brand" : "border-l-transparent text-text-secondary hover:text-text-primary"
      }`}
    >
      <item.Icon className="shrink-0" />
      {!iconOnly && <span className="text-body font-medium truncate">{item.label}</span>}
      {iconOnly && (
        <span className="pointer-events-none absolute left-full top-1/2 -translate-y-1/2 ml-2 whitespace-nowrap rounded-md bg-text-primary text-bg-surface px-2 py-1 text-small opacity-0 group-hover:opacity-100 transition-opacity duration-150 ease-out z-30 shadow-elevated">
          {item.label}
        </span>
      )}
    </button>
  );
}

/**
 * Persistent left rail, grouped by job-to-be-done (Understand/Operate/Prove),
 * labels-first -- icons are minimal single-color line marks, never the
 * primary signal. Collapses to an icon-only bottom bar at tablet width.
 */
export default function LeftRail({ active, onChange }) {
  return (
    <>
      <nav className="hidden xl:flex w-56 flex-col gap-6 py-6 bg-bg-surface border-r border-border-subtle shrink-0">
        <div className="px-4 text-label uppercase text-brand">DIYA</div>
        <div className="flex flex-col gap-6">
          {NAV_ITEMS.map((item) => (
            <div key={item.key}>
              <div className="px-4 text-label uppercase text-text-tertiary mb-1.5">{item.group}</div>
              <NavItem item={item} active={active} onChange={onChange} />
            </div>
          ))}
        </div>
      </nav>

      <nav className="flex xl:hidden fixed bottom-0 left-0 right-0 h-14 items-center justify-center gap-6 bg-bg-surface border-t border-border-subtle z-20">
        {NAV_ITEMS.map((item) => (
          <NavItem key={item.key} item={item} active={active} onChange={onChange} iconOnly />
        ))}
      </nav>
    </>
  );
}
