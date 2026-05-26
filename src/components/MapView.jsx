import { useState, useEffect } from 'react';
import { MapContainer, TileLayer, Marker, Popup, Circle, useMap } from 'react-leaflet';
import L from 'leaflet';
import { computeStatus, STATUS_META, STATUSES } from '../data/traps';
import TrapDetail from './TrapDetail';

delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

const STATUS_COLORS = {
  [STATUSES.EMPTY]: '#64748b',
  [STATUSES.ACTIVITY]: '#0284c7',
  [STATUSES.LIKELY_CATCH]: '#16a34a',
  [STATUSES.NEEDS_CHECK]: '#d97706',
  [STATUSES.THEFT]: '#dc2626',
};

function createTrapIcon(color, pulse) {
  return L.divIcon({
    className: '',
    html: `<div style="
      width:28px;height:28px;border-radius:50%;
      background:${color};border:3px solid white;
      box-shadow:0 0 0 3px ${color}44, 0 2px 8px rgba(0,0,0,0.5);
      display:flex;align-items:center;justify-content:center;
      font-size:12px;
      ${pulse ? `animation:pulse 1.5s infinite;` : ''}
    ">🦀</div>
    <style>@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.7;transform:scale(1.1)}}</style>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

function MapFitter({ traps }) {
  const map = useMap();
  useEffect(() => {
    if (traps.length) {
      const bounds = L.latLngBounds(traps.map(t => [t.lat, t.lng]));
      map.fitBounds(bounds, { padding: [40, 40] });
    }
  }, []);
  return null;
}

function timeAgo(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function MapView({ traps }) {
  const [selected, setSelected] = useState(null);
  const center = [44.655, -63.57];

  return (
    <div>
      <div className="section-header" style={{ marginBottom: 12 }}>
        <span className="section-title">Map View</span>
        <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>{traps.length} traps plotted</span>
      </div>

      <div className="map-wrapper card">
        <MapContainer center={center} zoom={13} style={{ height: '60vh', minHeight: 320 }}>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <MapFitter traps={traps} />
          {traps.map(trap => {
            const status = computeStatus(trap);
            const color = STATUS_COLORS[status];
            const isPulse = [STATUSES.LIKELY_CATCH, STATUSES.THEFT, STATUSES.ACTIVITY].includes(status);
            return (
              <Marker
                key={trap.id}
                position={[trap.lat, trap.lng]}
                icon={createTrapIcon(color, isPulse)}
                eventHandlers={{ click: () => setSelected(trap) }}
              >
                <Popup>
                  <div style={{ minWidth: 160, fontFamily: 'sans-serif' }}>
                    <strong style={{ color: '#0c1a2e' }}>{trap.name}</strong>
                    <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>{trap.id}</div>
                    <span style={{
                      background: STATUS_META[status].bg, color: STATUS_META[status].color,
                      padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600
                    }}>{STATUS_META[status].icon} {status}</span>
                    <div style={{ fontSize: 12, color: '#64748b', marginTop: 6 }}>
                      <div>Depth: {trap.depth}m</div>
                      <div>Battery: {trap.battery}%</div>
                      <div>Last activity: {timeAgo(trap.lastActivity)}</div>
                    </div>
                    <button
                      onClick={() => setSelected(trap)}
                      style={{
                        marginTop: 8, width: '100%', padding: '4px 0',
                        background: '#0369a1', color: 'white', border: 'none',
                        borderRadius: 6, cursor: 'pointer', fontSize: 12
                      }}
                    >View Details</button>
                  </div>
                </Popup>
              </Marker>
            );
          })}
          {traps.filter(t => t.gpsDeviation > 0.05).map(trap => (
            <Circle
              key={`drift-${trap.id}`}
              center={[trap.homeLat, trap.homeLng]}
              radius={60}
              pathOptions={{ color: '#dc2626', fillColor: '#dc2626', fillOpacity: 0.08, dashArray: '6 4' }}
            />
          ))}
        </MapContainer>
        <div className="map-legend">
          {Object.entries(STATUS_COLORS).map(([status, color]) => (
            <div key={status} className="legend-item">
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: color }} />
              {status}
            </div>
          ))}
          <div className="legend-item">
            <div style={{ width: 10, height: 10, border: '2px dashed #dc2626', borderRadius: '50%' }} />
            Home position
          </div>
        </div>
      </div>

      {selected && <TrapDetail trap={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
