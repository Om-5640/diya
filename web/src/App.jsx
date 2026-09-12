import { useState } from "react";
import AppShell from "./components/AppShell";
import OperatorLive from "./views/OperatorLive";
import Evidence from "./views/Evidence";

export default function App() {
  const [view, setView] = useState("operator");

  return (
    <AppShell active={view} onChangeView={setView}>
      {view === "operator" ? <OperatorLive /> : <Evidence />}
    </AppShell>
  );
}
