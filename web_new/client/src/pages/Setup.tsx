import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { ArrowLeft, ArrowRight, LocateFixed, MapPinned, Search, ShieldCheck } from "lucide-react";
import { apiPost, getApiErrorMessage, isFiniteNumber, persistLastSiteId } from "@/lib/api";
import { Shell } from "@/components/Shell";
import SiteMap from "@/components/SiteMap";

type LocationResult = { display_name: string; lat: number; lon: number; address?: Record<string, string> };
const NOMINATIM_BASE = "https://nominatim.openstreetmap.org";
const searchCache = new Map<string, LocationResult[]>();

function toLocationResult(item: { display_name: string; lat: string; lon: string; address?: Record<string, string> }): LocationResult | null {
  const lat = Number(item.lat);
  const lon = Number(item.lon);
  return Number.isFinite(lat) && Number.isFinite(lon) ? { display_name: item.display_name, lat, lon, address: item.address } : null;
}

async function nominatimRequest<T>(path: string, params: Record<string, string>, signal?: AbortSignal): Promise<T> {
  const url = new URL(`${NOMINATIM_BASE}${path}`);
  Object.entries(params).forEach(([key, value]) => url.searchParams.set(key, value));
  // Browsers own the forbidden User-Agent header. Accept-Language is supported here;
  // when this module is ever used in a server runtime, the runtime may add a descriptive User-Agent.
  const headers: HeadersInit = { "Accept-Language": "en" };
  if (typeof window === "undefined") headers["User-Agent"] = "DIYA/1.0 (microgrid setup)";
  const response = await fetch(url, { headers, signal });
  if (response.status === 429) throw new Error("Location search is temporarily unavailable. Please wait a moment and try again.");
  if (!response.ok) throw new Error("Location search is temporarily unavailable.");
  return response.json() as Promise<T>;
}

async function searchPlaces(query: string, signal?: AbortSignal) {
  const normalized = query.trim().toLowerCase();
  if (searchCache.has(normalized)) return searchCache.get(normalized) ?? [];
  const items = await nominatimRequest<Array<{ display_name: string; lat: string; lon: string; address?: Record<string, string> }>>("/search", { q: query.trim(), format: "json", limit: "5", addressdetails: "1" }, signal);
  const results = items.map(toLocationResult).filter((item): item is LocationResult => !!item);
  searchCache.set(normalized, results);
  return results;
}

async function reverseGeocode(lat: number, lon: number) {
  const item = await nominatimRequest<{ display_name: string; lat: string; lon: string; address?: Record<string, string> }>("/reverse", { lat: String(lat), lon: String(lon), format: "json", addressdetails: "1" });
  const result = toLocationResult(item);
  if (!result) throw new Error("Could not resolve your current location. Search manually instead.");
  return result;
}

function locationHint(result: LocationResult) {
  if (!result.address) return "OpenStreetMap result";
  return [result.address.city ?? result.address.town ?? result.address.village, result.address.state ?? result.address.county, result.address.country].filter(Boolean).join(" · ");
}

export default function Setup() {
  const [, navigate] = useLocation();
  const [step, setStep] = useState(1);
  const [locationMode, setLocationMode] = useState<"search" | "manual">("search");
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<LocationResult[]>([]);
  const [highlighted, setHighlighted] = useState(-1);
  const [searching, setSearching] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [resolved, setResolved] = useState<LocationResult | null>(null);
  const [manual, setManual] = useState({ lat: "", lon: "" });
  const [equipment, setEquipment] = useState({ pv_capacity_kwp: "", battery_capacity_kwh: "", diesel_rated_kw: "" });
  const [economics, setEconomics] = useState({ diesel_price_inr_per_l: "", outage_cost_inr_per_kwh: "" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const searchAbort = useRef<AbortController | null>(null);
  const searchRequestId = useRef(0);

  useEffect(() => {
    const trimmed = query.trim();
    if (locationMode !== "search" || trimmed.length < 3 || resolved?.display_name === query) {
      setSuggestions([]); setDropdownOpen(false); setSearching(false); return;
    }
    setSearching(true); setDropdownOpen(true); setHighlighted(-1); setMessage("");
    const requestId = ++searchRequestId.current;
    const timer = window.setTimeout(async () => {
      searchAbort.current?.abort();
      const controller = new AbortController(); searchAbort.current = controller;
      try {
        const results = await searchPlaces(trimmed, controller.signal);
        if (requestId !== searchRequestId.current) return;
        setSuggestions(results); setDropdownOpen(true); setMessage(results.length ? "" : "No locations found.");
      } catch (error) {
        if (controller.signal.aborted) return;
        console.error("[DIYA] Location search failed", error);
        setSuggestions([]); setMessage(getApiErrorMessage(error, "Location search is temporarily unavailable."));
      } finally { if (requestId === searchRequestId.current) setSearching(false); }
    }, 450);
    return () => window.clearTimeout(timer);
  }, [query, locationMode, resolved?.display_name]);

  const selectLocation = (result: LocationResult) => {
    setResolved(result); setQuery(result.display_name); setSuggestions([]); setDropdownOpen(false); setHighlighted(-1); setMessage("Location selected. Review the coordinates before continuing.");
  };

  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") { setDropdownOpen(false); setHighlighted(-1); return; }
    if (event.key === "ArrowDown") { event.preventDefault(); setDropdownOpen(true); setHighlighted(index => Math.min(index + 1, suggestions.length - 1)); return; }
    if (event.key === "ArrowUp") { event.preventDefault(); setHighlighted(index => Math.max(index - 1, 0)); return; }
    if (event.key === "Enter" && dropdownOpen && suggestions.length) { event.preventDefault(); selectLocation(suggestions[highlighted >= 0 ? highlighted : 0]); }
  };

  const resolveSearch = async (event?: FormEvent) => {
    event?.preventDefault();
    const trimmed = query.trim();
    if (trimmed.length < 3) { setMessage("Enter at least 3 characters to search for a location."); return; }
    setSearching(true); setMessage("");
    try { const results = await searchPlaces(trimmed); if (!results.length) { setSuggestions([]); setDropdownOpen(true); setMessage("No locations found."); return; } selectLocation(results[0]); }
    catch (error) { console.error("[DIYA] Location search failed", error); setMessage(getApiErrorMessage(error, "Location search is temporarily unavailable.")); }
    finally { setSearching(false); }
  };

  const useBrowserLocation = () => {
    if (!navigator.geolocation) { setMessage("Your browser does not provide location access. Search manually instead."); return; }
    setBusy(true); setMessage("");
    navigator.geolocation.getCurrentPosition(async position => {
      try { const { latitude: lat, longitude: lon } = position.coords; const result = await reverseGeocode(lat, lon); setResolved(result); setQuery(result.display_name); setMessage("Current location resolved through OpenStreetMap."); }
      catch (error) { console.error("[DIYA] Reverse geocoding failed", error); setMessage(getApiErrorMessage(error, "Could not resolve your current location. Search manually instead.")); }
      finally { setBusy(false); }
    }, error => { setBusy(false); setMessage(error.code === error.PERMISSION_DENIED ? "Location permission was denied. Search manually instead." : "Could not resolve your current location. Search manually instead."); }, { enableHighAccuracy: false, timeout: 60000 });
  };

  const setManualLocation = () => {
    const lat = Number(manual.lat), lon = Number(manual.lon);
    if (!isFiniteNumber(lat) || !isFiniteNumber(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) { setMessage("Enter valid coordinates: latitude −90 to 90 and longitude −180 to 180."); return; }
    setResolved({ display_name: `${lat}, ${lon}`, lat, lon }); setQuery(`${lat}, ${lon}`); setMessage("Coordinates selected. Review the map before continuing.");
  };

  const createSite = async (event: FormEvent) => {
    event.preventDefault(); if (!resolved) { setMessage("Select or resolve a location before creating your site."); return; }
    setBusy(true); setMessage("");
    const body: Record<string, unknown> = { display_name: resolved.display_name, lat: resolved.lat, lon: resolved.lon, pv_capacity_kwp: Number(equipment.pv_capacity_kwp), battery_capacity_kwh: Number(equipment.battery_capacity_kwh), diesel_price_inr_per_l: Number(economics.diesel_price_inr_per_l), outage_cost_inr_per_kwh: Number(economics.outage_cost_inr_per_kwh) };
    if (equipment.diesel_rated_kw.trim() !== "") body.diesel_rated_kw = Number(equipment.diesel_rated_kw);
    try { const result = await apiPost<{ site_id: string }>("/api/sites", body); persistLastSiteId(result.site_id); navigate(`/site/${result.site_id}`); }
    catch (error) { console.error("[DIYA] Site creation failed", error); setMessage(getApiErrorMessage(error, "Couldn't create your site. Please check the configuration and try again.")); }
    finally { setBusy(false); }
  };
  const canContinue = step === 1 ? !!resolved : true;
  return <Shell><div className="setup-page">
    <section className="setup-intro"><div className="eyebrow">A QUIETER WAY TO OPERATE A MICROGRID</div><h1>Make the next<br /><em>energy decision</em><br />clear.</h1><p>DIYA turns real weather, renewable physics, and configurable economics into a plan you can trust in the field.</p><div className="intro-note"><ShieldCheck size={17} /><span>Frontend connected to the live DIYA optimization API. No synthetic site data is used.</span></div></section>
    <section className="setup-panel">
      <div className="setup-progress"><span className={step >= 1 ? "current" : ""}>01 <b>Location</b></span><i /><span className={step >= 2 ? "current" : ""}>02 <b>Equipment</b></span><i /><span className={step >= 3 ? "current" : ""}>03 <b>Economics</b></span></div>
      {step === 1 && <div className="wizard-step"><div className="eyebrow">STEP 01 / LOCATION</div><h2>Where is your microgrid?</h2><p className="step-copy">Search the real map, choose a result, and confirm the exact coordinates before continuing.</p><div className="segmented"><button type="button" className={locationMode === "search" ? "selected" : ""} onClick={() => setLocationMode("search")}><Search size={15} /> Search by place</button><button type="button" className={locationMode === "manual" ? "selected" : ""} onClick={() => setLocationMode("manual")}><MapPinned size={15} /> Enter coordinates</button></div>{locationMode === "search" ? <form onSubmit={resolveSearch} className="location-search-wrap"><div className="input-row"><div className="location-input-wrap"><Search size={16} /><input value={query} onChange={e => { setQuery(e.target.value); if (resolved) setResolved(null); }} onFocus={() => suggestions.length && setDropdownOpen(true)} onKeyDown={handleSearchKeyDown} placeholder="Search for a city, village, district..." aria-label="Search for a city, village, district" aria-autocomplete="list" aria-controls="location-results" aria-expanded={dropdownOpen} autoComplete="off" />{searching && <span className="input-status">Searching…</span>}</div><button className="button button-primary" disabled={searching || query.trim().length < 3}>{searching ? "Searching…" : "Search"}</button></div>{dropdownOpen && <div id="location-results" className="location-results" role="listbox">{suggestions.map((result, index) => <button type="button" key={`${result.lat}-${result.lon}`} className={`location-result ${index === highlighted ? "highlighted" : ""}`} role="option" aria-selected={index === highlighted} onMouseDown={event => { event.preventDefault(); selectLocation(result); }}><span className="result-icon"><MapPinned size={14} /></span><span><strong>{result.display_name}</strong><small>{locationHint(result)}</small></span></button>)}{!searching && !suggestions.length && query.trim().length >= 3 && <div className="location-empty">No locations found.</div>}</div>}</form> : <div className="coordinate-row"><input value={manual.lat} onChange={e => setManual({ ...manual, lat: e.target.value })} placeholder="Latitude" inputMode="decimal" aria-label="Latitude" /><input value={manual.lon} onChange={e => setManual({ ...manual, lon: e.target.value })} placeholder="Longitude" inputMode="decimal" aria-label="Longitude" /><button type="button" className="button button-primary" onClick={setManualLocation}>Use coordinates</button></div>}<button type="button" className="use-location" onClick={useBrowserLocation} disabled={busy}><LocateFixed size={15} /> Use my location</button>{message && <p className="form-message">{message}</p>}{resolved && <div className="map-confirm"><SiteMap lat={resolved.lat} lon={resolved.lon} label={resolved.display_name} /><div className="map-selection"><div><span className="confirmation-label">Location confirmed</span><strong>{resolved.display_name}</strong></div><span>{resolved.lat}, {resolved.lon}</span></div><div className="map-caption">Exact coordinates from OpenStreetMap · sent to the live DIYA plan</div></div>}<div className="wizard-actions"><span /> <button type="button" className="button button-primary" disabled={!canContinue} onClick={() => setStep(2)}>Continue to equipment <ArrowRight size={16} /></button></div></div>}
      {step === 2 && <div className="wizard-step"><div className="eyebrow">STEP 02 / EQUIPMENT</div><h2>What can your site do?</h2><p className="step-copy">Tell DIYA the nameplate capacity available to the dispatch plan.</p><div className="form-grid equipment-grid"><label className="equipment-field solar-field"><span className="equipment-kicker">01 / SOLAR</span>PV capacity <span>kWp</span><input required type="number" min="0" step="any" value={equipment.pv_capacity_kwp} onChange={e => setEquipment({ ...equipment, pv_capacity_kwp: e.target.value })} placeholder="Capacity" /></label><label className="equipment-field battery-field"><span className="equipment-kicker">02 / STORAGE</span>Battery capacity <span>kWh</span><input required type="number" min="0" step="any" value={equipment.battery_capacity_kwh} onChange={e => setEquipment({ ...equipment, battery_capacity_kwh: e.target.value })} placeholder="Capacity" /></label><label className="equipment-field diesel-field wide"><span className="equipment-kicker">03 / BACKUP</span>Diesel generator capacity <span>kW · optional</span><input type="number" min="0" step="any" value={equipment.diesel_rated_kw} onChange={e => setEquipment({ ...equipment, diesel_rated_kw: e.target.value })} placeholder="Leave blank if your site has no diesel generator" /></label></div>{message && <p className="form-message">{message}</p>}<div className="wizard-actions"><button type="button" className="button button-ghost" onClick={() => setStep(1)}><ArrowLeft size={16} /> Back</button><button type="button" className="button button-primary" disabled={!equipment.pv_capacity_kwp || !equipment.battery_capacity_kwh} onClick={() => setStep(3)}>Continue to economics <ArrowRight size={16} /></button></div></div>}
      {step === 3 && <form className="wizard-step" onSubmit={createSite}><div className="eyebrow">STEP 03 / ECONOMICS</div><h2>What does resilience mean here?</h2><p className="step-copy">These are configurable assumptions used by the optimizer, not utility bills.</p><div className="form-grid"><label>Diesel price <span>INR / litre</span><input required type="number" min="0" step="any" value={economics.diesel_price_inr_per_l} onChange={e => setEconomics({ ...economics, diesel_price_inr_per_l: e.target.value })} placeholder="Price" /></label><label>Cost of an outage <span>INR / kWh</span><input required type="number" min="0" step="any" value={economics.outage_cost_inr_per_kwh} onChange={e => setEconomics({ ...economics, outage_cost_inr_per_kwh: e.target.value })} placeholder="Impact" /></label></div><div className="tradeoff-line"><span>COST</span><i /><span>CLEAN ENERGY</span><i /><span>RELIABILITY</span></div><div className="assumption-note"><span className="status-dot" /><span>DIYA will make the trade-off between cost, clean energy, and critical-load safety explicit.</span></div>{message && <p className="form-message">{message}</p>}<div className="wizard-actions"><button type="button" className="button button-ghost" onClick={() => setStep(2)}><ArrowLeft size={16} /> Back</button><button className="button button-primary" disabled={busy}>{busy ? "Creating your site…" : "Create my site"} <ArrowRight size={16} /></button></div></form>}
    </section>
  </div></Shell>;
}
