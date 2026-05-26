import { computeStatus, STATUS_META } from '../data/traps';

function timeAgo(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function SensorBar({ value, max, color }) {
  const pct = Math.min(100, (value / max) * 100);
  return (
    <div className="s-bar">
      <div className="s-bar-fill" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

const EVENT_COLORS = { catch: '#16a34a', activity: '#0ea5e9', alert: '#dc2626', check: '#94a3b8', set: '#d97706' };

export default function TrapDetail({ trap, onClose }) {
  const status = computeStatus(trap);
  const meta = STATUS_META[status];
  const battColor = trap.battery > 50 ? '#16a34a' : trap.battery > 20 ? '#d97706' : '#dc2626';

  return (
    <div className="overlay" onClick={onClose}>
      <div className="detail-panel" onClick={e => e.stopPropagation()}>
        <button className="detail-close" onClick={onClose}>✕</button>
        <div className="detail-title">{trap.name}</div>
        <div className="detail-id">{trap.id} · {trap.depth}m depth · Last checked {timeAgo(trap.lastChecked)}</div>

        <span className="status-badge" style={{ background: meta.bg, color: meta.color }}>
          {meta.icon} {status}
        </span>

        <div className="divider" />
        <div className="section-title" style={{ marginBottom: 10 }}>Sensor Readings</div>
        <div className="sensors-grid">
          <div className="sensor-card">
            <div className="s-label">Movement</div>
            <div className="s-value" style={{ color: trap.movement > 5 ? '#f87171' : '#38bdf8' }}>{trap.movement.toFixed(1)}</div>
            <SensorBar value={trap.movement} max={12} color={trap.movement > 5 ? '#f87171' : '#38bdf8'} />
          </div>
          <div className="sensor-card">
            <div className="s-label">Vibration</div>
            <div className="s-value" style={{ color: trap.vibration > 5 ? '#f87171' : '#38bdf8' }}>{trap.vibration.toFixed(1)}</div>
            <SensorBar value={trap.vibration} max={15} color={trap.vibration > 5 ? '#f87171' : '#38bdf8'} />
          </div>
          <div className="sensor-card">
            <div className="s-label">Bait Disturbance</div>
            <div className="s-value" style={{ color: trap.baitDisturbance > 50 ? '#16a34a' : '#38bdf8' }}>{trap.baitDisturbance}%</div>
            <SensorBar value={trap.baitDisturbance} max={100} color={trap.baitDisturbance > 50 ? '#16a34a' : '#38bdf8'} />
          </div>
          <div className="sensor-card">
            <div className="s-label">Weight Change</div>
            <div className="s-value" style={{ color: trap.weightChange > 1 ? '#16a34a' : '#38bdf8' }}>+{trap.weightChange.toFixed(1)} kg</div>
            <SensorBar value={trap.weightChange} max={5} color={trap.weightChange > 1 ? '#16a34a' : '#38bdf8'} />
          </div>
          <div className="sensor-card">
            <div className="s-label">Water Depth</div>
            <div className="s-value">{trap.depth} m</div>
            <SensorBar value={trap.depth} max={40} color="#0ea5e9" />
          </div>
          <div className="sensor-card">
            <div className="s-label">GPS Deviation</div>
            <div className="s-value" style={{ color: trap.gpsDeviation > 0.05 ? '#dc2626' : '#38bdf8' }}>
              {(trap.gpsDeviation * 1000).toFixed(0)} m
            </div>
            <SensorBar value={trap.gpsDeviation * 1000} max={200} color={trap.gpsDeviation > 0.05 ? '#dc2626' : '#38bdf8'} />
          </div>
          <div className="sensor-card">
            <div className="s-label">Battery</div>
            <div className="s-value" style={{ color: battColor }}>{trap.battery}%</div>
            <SensorBar value={trap.battery} max={100} color={battColor} />
          </div>
          <div className="sensor-card">
            <div className="s-label">Last Activity</div>
            <div className="s-value" style={{ fontSize: '0.85rem' }}>{timeAgo(trap.lastActivity)}</div>
          </div>
        </div>

        <div className="divider" />
        <div className="section-title" style={{ marginBottom: 10 }}>GPS Position</div>
        <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginBottom: 16, display: 'flex', gap: 16 }}>
          <span>Lat: {trap.lat.toFixed(4)}</span>
          <span>Lng: {trap.lng.toFixed(4)}</span>
          {trap.gpsDeviation > 0.05 && (
            <span style={{ color: '#dc2626', fontWeight: 600 }}>⚠ Moved {(trap.gpsDeviation * 1000).toFixed(0)}m from home</span>
          )}
        </div>

        <div className="section-title" style={{ marginBottom: 10 }}>Activity Log</div>
        <ul className="history-list">
          {trap.history.map((h, i) => (
            <li key={i} className="history-item">
              <div className="history-dot" style={{ background: EVENT_COLORS[h.type] || '#94a3b8', marginTop: 6 }} />
              <div>
                <div className="history-event">{h.event}</div>
                <div className="history-time">{timeAgo(h.time)}</div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
