import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

export default function SiteMap({ lat, lon, label }: { lat: number; lon: number; label: string }) {
  const nodeRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!nodeRef.current) return;
    const map = L.map(nodeRef.current, { zoomControl: false, attributionControl: true }).setView([lat, lon], 11);
    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" }).addTo(map);
    const marker = L.divIcon({ className: "diya-map-marker", html: `<span></span>`, iconSize: [18, 18], iconAnchor: [9, 9] });
    L.marker([lat, lon], { icon: marker, title: label }).addTo(map).bindTooltip(label, { direction: "top", offset: [0, -10] }).openTooltip();
    return () => { map.remove(); };
  }, [lat, lon, label]);
  return <div ref={nodeRef} className="leaflet-map" aria-label={`Map showing ${label}`} />;
}
