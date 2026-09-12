import LeftRail from "./LeftRail";

export default function AppShell({ active, onChangeRoute, children }) {
  return (
    <div className="min-h-screen flex bg-bg-canvas text-text-primary">
      <LeftRail active={active} onChange={onChangeRoute} />
      <div className="flex-1 flex flex-col min-w-0 pb-14 xl:pb-0">
        <main className="flex-1 min-w-0">{children}</main>
      </div>
    </div>
  );
}
