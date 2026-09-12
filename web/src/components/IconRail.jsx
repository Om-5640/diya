function OperatorIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M3 12h4l2 7 4-14 2 7h6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function EvidenceIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="4" y="11" width="4" height="9" />
      <rect x="10" y="7" width="4" height="13" />
      <rect x="16" y="4" width="4" height="16" />
    </svg>
  );
}

const NAV_ITEMS = [
  { key: "operator", label: "Operator Live", Icon: OperatorIcon },
  { key: "evidence", label: "Evidence", Icon: EvidenceIcon },
];

function NavButton({ item, active, onChange }) {
  const isActive = active === item.key;
  return (
    <button
      type="button"
      onClick={() => onChange(item.key)}
      title={item.label}
      className={`group relative w-12 h-12 flex items-center justify-center rounded transition-colors duration-150 ease-out border-l-2 ${
        isActive
          ? "border-l-accent-battery bg-bg-panel-raised text-text-primary"
          : "border-l-transparent text-text-tertiary hover:text-text-secondary"
      }`}
    >
      <item.Icon />
      <span className="pointer-events-none absolute left-full top-1/2 -translate-y-1/2 ml-2 whitespace-nowrap rounded-sm bg-bg-panel-raised border border-border-subtle px-2 py-1 text-meta text-text-primary opacity-0 group-hover:opacity-100 transition-opacity duration-150 ease-out z-30">
        {item.label}
      </span>
    </button>
  );
}

export default function IconRail({ active, onChange }) {
  return (
    <>
      {/* Desktop: slim vertical icon rail (>=1280px) */}
      <nav className="hidden xl:flex w-16 flex-col items-center gap-2 py-4 bg-bg-panel border-r border-border-subtle">
        {NAV_ITEMS.map((item) => (
          <NavButton key={item.key} item={item} active={active} onChange={onChange} />
        ))}
      </nav>

      {/* Tablet/mobile: icon-only bottom bar, centered (<1280px) */}
      <nav className="flex xl:hidden fixed bottom-0 left-0 right-0 h-14 items-center justify-center gap-6 bg-bg-panel border-t border-border-subtle z-20">
        {NAV_ITEMS.map((item) => (
          <NavButton key={item.key} item={item} active={active} onChange={onChange} />
        ))}
      </nav>
    </>
  );
}
