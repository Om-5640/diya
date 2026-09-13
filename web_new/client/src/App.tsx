import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Route, Switch } from "wouter";
import ErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider } from "./contexts/ThemeContext";
import Setup from "./pages/Setup";
import Overview from "./pages/Overview";
import Dispatch from "./pages/Dispatch";
import Evidence from "./pages/Evidence";
import Manage from "./pages/Manage";
import NotFound from "./pages/NotFound";

function Router() {
  return <Switch>
    <Route path="/" component={Setup} />
    <Route path="/site/:siteId" component={Overview} />
    <Route path="/site/:siteId/dispatch" component={Dispatch} />
    <Route path="/site/:siteId/evidence" component={Evidence} />
    <Route path="/site/:siteId/manage" component={Manage} />
    <Route component={NotFound} />
  </Switch>;
}

export default function App() {
  return <ErrorBoundary><ThemeProvider defaultTheme="light"><TooltipProvider><Toaster /><Router /></TooltipProvider></ThemeProvider></ErrorBoundary>;
}
