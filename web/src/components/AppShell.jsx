import IconRail from "./IconRail";
import TopBar from "./TopBar";

export default function AppShell({ active, onChangeView, children }) {
  return (
    <div className="min-h-screen flex bg-bg-base text-text-primary">
      <IconRail active={active} onChange={onChangeView} />
      <div className="flex-1 flex flex-col min-w-0 pb-14 xl:pb-0">
        <TopBar />
        <main className="flex-1 min-w-0">{children}</main>
      </div>
    </div>
  );
}
