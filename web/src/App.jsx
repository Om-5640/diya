import { useState } from "react";
import AppShell from "./components/AppShell";
import Overview from "./routes/Overview";
import LiveDispatch from "./routes/LiveDispatch";
import Evidence from "./routes/Evidence";
import { useLiveDispatch } from "./hooks/useLiveDispatch";

export default function App() {
  const [route, setRoute] = useState("overview");
  // Lifted here (not called inside Overview/LiveDispatch individually) so
  // switching between those two routes shares one fetch instead of
  // re-triggering /api/solve_live on every navigation.
  const liveDispatch = useLiveDispatch();

  return (
    <AppShell active={route} onChangeRoute={setRoute}>
      {route === "overview" && <Overview onNavigate={setRoute} liveDispatch={liveDispatch} />}
      {route === "live" && <LiveDispatch liveDispatch={liveDispatch} />}
      {route === "evidence" && <Evidence />}
    </AppShell>
  );
}
