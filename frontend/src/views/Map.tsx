import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

import KbdFooter from "../components/KbdFooter";
import Rail from "../components/Rail";
import StatusRow from "../components/StatusRow";
import Topbar from "../components/Topbar";

interface MapMarker {
  lat: number;
  lon: number;
  count: number;
  cover_sha256: string;
  cover_url: string;
  place: string | null;
}

function photoIcon(url: string, count: number): L.DivIcon {
  return L.divIcon({
    className: "photo-marker",
    html: `
      <div style="position:relative;width:56px;height:56px;">
        <img src="${url}" style="width:56px;height:56px;object-fit:cover;border-radius:8px;
          border:3px solid #fff;box-shadow:0 4px 14px rgba(0,0,0,.4);"/>
        <span style="position:absolute;bottom:-4px;right:-4px;background:#1A5DCC;color:#fff;
          font-family:'Google Sans Code',monospace;font-size:11px;font-weight:700;
          padding:2px 6px;border-radius:10px;border:2px solid #fff;">${count}</span>
      </div>
    `,
    iconSize: [56, 56],
    iconAnchor: [28, 28],
  });
}

export default function MapView() {
  const [markers, setMarkers] = useState<MapMarker[]>([]);
  const [loading, setLoading] = useState(true);
  const mapDivRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);

  useEffect(() => {
    fetch("/api/map/markers?grid_deg=0.01")
      .then((r) => r.json())
      .then((d) => { setMarkers(d.markers); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  // Initialize Leaflet once when markers arrive
  useEffect(() => {
    if (!mapDivRef.current || markers.length === 0) return;
    if (mapRef.current) {
      // Clear and re-add markers
      mapRef.current.eachLayer((layer) => {
        if (layer instanceof L.Marker) mapRef.current?.removeLayer(layer);
      });
    } else {
      mapRef.current = L.map(mapDivRef.current, { scrollWheelZoom: true });
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }).addTo(mapRef.current);
    }

    markers.forEach((m) => {
      const marker = L.marker([m.lat, m.lon], { icon: photoIcon(m.cover_url, m.count) }).addTo(mapRef.current!);
      const place = m.place || "Unnamed location";
      const clusterLink = m.place
        ? `<a href="/cull/clusters/${encodeURIComponent(m.place)}" style="color:#1A5DCC;font-size:12px;">Open cluster →</a>`
        : "";
      marker.bindPopup(`
        <div style="min-width:160px;">
          <img src="${m.cover_url}" style="width:100%;border-radius:6px;" alt=""/>
          <div style="margin-top:6px;font-family:'Google Sans Display',sans-serif;font-weight:500;">${place}</div>
          <div style="font-family:'Google Sans Code',monospace;font-size:11px;color:#666;">
            ${m.count} photo${m.count !== 1 ? "s" : ""} · ${m.lat.toFixed(4)}, ${m.lon.toFixed(4)}
          </div>
          <div style="margin-top:4px;">${clusterLink}</div>
        </div>
      `);
    });

    const bounds = L.latLngBounds(markers.map((m) => [m.lat, m.lon] as [number, number]));
    mapRef.current.fitBounds(bounds, { padding: [60, 60] });

    return () => {
      // keep map alive across re-renders
    };
  }, [markers]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, []);

  const totalPhotos = markers.reduce((s, m) => s + m.count, 0);

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="selects" context="map" />
        <StatusRow
          pos={`${markers.length} locations`}
          keepersCount={totalPhotos}
          details={loading ? "loading…" : `${totalPhotos} photos with GPS`}
        />

        <div style={{ gridColumn: 1, gridRow: "3 / span 3", padding: "16px 24px", overflow: "hidden", background: "var(--md-surface)" }}>
          <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
            <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontWeight: 500, fontSize: 26 }}>Map</h1>
            <span style={{ flex: 1 }} />
            <span style={{ color: "var(--md-on-surface-var)", fontSize: 13 }}>
              Tap a pin to jump to that location's cluster
            </span>
          </div>

          <div
            ref={mapDivRef}
            style={{
              height: "calc(100vh - 250px)",
              borderRadius: 16,
              overflow: "hidden",
              border: "1px solid var(--md-outline-var)",
              background: "var(--md-surface-c-low)",
            }}
          >
            {markers.length === 0 && !loading && (
              <div style={{ display: "grid", placeItems: "center", height: "100%", color: "var(--md-on-surface-var)" }}>
                No photos with GPS metadata yet.
              </div>
            )}
          </div>
        </div>

        <KbdFooter />
      </div>
    </div>
  );
}
