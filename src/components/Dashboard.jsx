import { useState } from 'react';
import { computeStatus, STATUS_META, STATUSES } from '../data/traps';
import TrapDetail from './TrapDetail';

function timeAgo(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function BatteryBar({ level }) {
  const color = level > 50 ? '#16a34a' : level > 20 ? '#d97706' : '#dc2626';
  return (
    <div className="battery-bar">
      <span style={{ fontSize: '0.8rem' }}>🔋</span>
      <div className="battery-track">
        <div className="battery-fill" style={{ width: `${level}%`, background: color }} />
      </div>
      <span className="battery-pct">{level}%</span>
    </div>
  );
}

function TrapCard({ trap, onClick }) {
  const status = computeStatus(trap);
  const meta = STATUS_META[status];
  const hoursSince = (Date.now() - new Date(trap.lastActivity).getTime()) / 36e5;

  return (
    <div className="trap-card" onClick={() => onClick(trap)}>
      <div className="trap-card-header">
        <div>
          <div className="trap-id">{trap.id}</div>
          <div className="trap-name">{trap.name}</div>
        </div>
        <span className="status-badge" style={{ background: meta.bg, color: meta.color }}>
          {meta.icon} {status}
        </span>
      </div>

      <div className="trap-sensors">
        <div className="sensor-item">
          <span className="sensor-label">Movement</span>
          <span className="sensor-value">{trap.movement.toFixed(1)}</span>
        </div>
        <div className="sensor-item">
          <span className="sensor-label">Weight Change</span>
          <span className="sensor-value">+{trap.weightChange.toFixed(1)} kg</span>
        </div>
        <div className="sensor-item">
          <span className="sensor-label">Bait Disturbed</span>
          <span className="sensor-value">{trap.baitDisturbance}%</span>
        </div>
        <div className="sensor-item">
          <span className="sensor-label">Depth</span>
          <span className="sensor-value">{trap.depth} m</span>
        </div>
      </div>

      <div className="trap-footer">
        <div className="trap-meta">
          <div>Last activity: {timeAgo(trap.lastActivity)}</div>
          <div>Checked: {timeAgo(trap.lastChecked)}</div>
        </div>
        <BatteryBar level={trap.battery} />
      </div>
    </div>
  );
}

const ALL_FILTERS = ['All', ...Object.values(STATUSES)];

export default function Dashboard({ traps }) {
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState('All');

  const counts = Object.values(STATUSES).reduce((acc, s) => {
    acc[s] = traps.filter(t => computeStatus(t) === s).length;
    return acc;
  }, {});

  const alerts = traps.filter(t => [STATUSES.THEFT, STATUSES.LIKELY_CATCH].includes(computeStatus(t))).length;
  const needsCheck = traps.filter(t => computeStatus(t) === STATUSES.NEEDS_CHECK).length;
  const lowBatt = traps.filter(t => t.battery <= 20).length;

  const filtered = filter === 'All' ? traps : traps.filter(t => computeStatus(t) === filter);

  const sorted = [...filtered].sort((a, b) => {
    return STATUS_META[computeStatus(b)].priority - STATUS_META[computeStatus(a)].priority;
  });

  return (
    <div>
      <div className="summary-row">
        <div className="stat-card">
          <div className="stat-num" style={{ color: '#38bdf8' }}>{traps.length}</div>
          <div className="stat-label">Total Traps</div>
        </div>
        <div className="stat-card">
          <div className="stat-num" style={{ color: '#16a34a' }}>{counts[STATUSES.LIKELY_CATCH] || 0}</div>
          <div className="stat-label">Likely Catch</div>
        </div>
        <div className="stat-card">
          <div className="stat-num" style={{ color: '#0ea5e9' }}>{counts[STATUSES.ACTIVITY] || 0}</div>
          <div className="stat-label">Active</div>
        </div>
        <div className="stat-card">
          <div className="stat-num" style={{ color: '#d97706' }}>{needsCheck}</div>
          <div className="stat-label">Needs Check</div>
        </div>
        <div className="stat-card">
          <div className="stat-num" style={{ color: '#dc2626' }}>{counts[STATUSES.THEFT] || 0}</div>
          <div className="stat-label">Theft Alert</div>
        </div>
        <div className="stat-card">
          <div className="stat-num" style={{ color: lowBatt > 0 ? '#dc2626' : '#94a3b8' }}>{lowBatt}</div>
          <div className="stat-label">Low Battery</div>
        </div>
      </div>

      <div className="section-header">
        <span className="section-title">Trap Status</span>
        <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>{filtered.length} traps</span>
      </div>

      <div className="filter-row">
        {ALL_FILTERS.map(f => (
          <button key={f} className={`chip${filter === f ? ' active' : ''}`} onClick={() => setFilter(f)}>
            {f === 'All' ? `All (${traps.length})` : `${STATUS_META[f]?.icon} ${f} (${counts[f] || 0})`}
          </button>
        ))}
      </div>

      {sorted.length === 0 ? (
        <div className="empty-state">No traps matching this filter</div>
      ) : (
        <div className="traps-grid">
          {sorted.map(trap => (
            <TrapCard key={trap.id} trap={trap} onClick={setSelected} />
          ))}
        </div>
      )}

      {selected && <TrapDetail trap={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
