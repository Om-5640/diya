export default function TopBar() {
  return (
    <header className="flex items-center justify-between gap-4 px-6 py-5 border-b border-border-subtle flex-wrap">
      <div>
        <h1 className="text-h1 font-semibold text-text-primary">Khavda, Kutch</h1>
        <p className="text-meta text-text-secondary mt-1">180 households · 1 health centre</p>
      </div>
      <div className="inline-flex items-center gap-2 rounded-sm border border-border-subtle px-2.5 py-1">
        <span className="w-1.5 h-1.5 rounded-full bg-status-good inline-block" aria-hidden="true" />
        <span className="text-meta text-text-secondary font-medium tracking-wide">OFFLINE-CAPABLE</span>
      </div>
    </header>
  );
}
