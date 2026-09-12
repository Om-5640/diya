import { useCallback, useEffect, useRef, useState } from "react";
import { loadRun } from "../lib/loadRun";

const KHAVDA_LAT = 23.8443;
const KHAVDA_LON = 69.7317;
const CLIENT_TIMEOUT_MS = 5000;
const DEFAULT_SOC_PCT = 60;

async function fetchSolveLive(socPct) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CLIENT_TIMEOUT_MS);
  try {
    const res = await fetch("/api/solve_live", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: KHAVDA_LAT, lon: KHAVDA_LON, soc_pct: socPct }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`solve_live HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Shared live-or-static dispatch state for Overview and Live Dispatch (both
 * routes read the SAME current run so switching between them doesn't
 * re-fetch). On mount, attempts a live 72h /api/solve_live plan for Khavda;
 * on any failure (network down, non-200, or a client-side 5s timeout) it
 * falls back to the static S2_mpc.json data with a status-warn notice. This
 * is the exact logic from the pre-redesign OperatorLive view, only moved
 * into a hook so two routes can share one fetch -- behavior is unchanged.
 */
export function useLiveDispatch() {
  const [staticRun, setStaticRun] = useState(null);
  const [liveRun, setLiveRun] = useState(null);
  const [isLoadingLive, setIsLoadingLive] = useState(true);
  const [liveFailed, setLiveFailed] = useState(false);
  const [hasSettledOnce, setHasSettledOnce] = useState(false);
  const [socPct, setSocPct] = useState(DEFAULT_SOC_PCT);
  const requestSeq = useRef(0);

  useEffect(() => {
    let cancelled = false;
    loadRun("S2", "mpc")
      .then((data) => {
        if (!cancelled) setStaticRun(data);
      })
      .catch(() => {
        // static fallback failing too is a hard error case handled by the
        // "nothing to show yet" branch in each route
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const requestLive = useCallback((soc) => {
    const seq = ++requestSeq.current;
    setIsLoadingLive(true);
    fetchSolveLive(soc)
      .then((data) => {
        if (seq !== requestSeq.current) return;
        setLiveRun(data);
        setLiveFailed(false);
      })
      .catch(() => {
        if (seq !== requestSeq.current) return;
        setLiveRun(null);
        setLiveFailed(true);
      })
      .finally(() => {
        if (seq !== requestSeq.current) return;
        setIsLoadingLive(false);
        setHasSettledOnce(true);
      });
  }, []);

  useEffect(() => {
    requestLive(DEFAULT_SOC_PCT);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeRun = liveRun ?? staticRun;

  return {
    staticRun,
    liveRun,
    activeRun,
    isLoadingLive,
    liveFailed,
    hasSettledOnce,
    socPct,
    setSocPct,
    requestLive,
  };
}
