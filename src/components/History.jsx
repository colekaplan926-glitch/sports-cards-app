import { useState } from 'react';
import { computeStatus, STATUS_META } from '../data/traps';

function timeAgo(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const EVENT_STYLES = {
  catch:    { color: '#16a34a', icon: '🦀' },
  activity: { color: '#0ea5e9', icon: '📡' },
  alert:    { color: '#dc2626', icon: '🚨' },
  check:    { color: '#94a3b8', icon: '✅' },
  set:      { color: '#d97706', icon: '⚓' },
};

export default function History({ traps }) {
  const [trapFilter, setTrapFilter] = useState('All');
  const [typeFilter, setTypeFilter] = useState('All');

  const allEvents = traps.flatMap(trap =>
    trap.history.map(h => ({ ...h, trapId: trap.id, trapName: trap.name }))
  ).sort((a, b) => new Date(b.time) - new Date(a.time));

  const filtered = allEvents.filter(e =>
    (trapFilter === 'All' || e.trapId === trapFilter) &&
    (typeFilter === 'All' || e.type === typeFilter)
  );

  const trapOptions = ['All', ...traps.map(t => t.id)];
  const typeOptions = ['All', 'catch', 'activity', 'alert', 'check', 'set'];

  return (
    <div>
      <div className="section-header" style={{ marginBottom: 12 }}>
        <span className="section-title">Activity History</span>
        <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>{filtered.length} events</span>
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: '0.65rem', color: '#94a3b8', textTransform: 'uppercase', marginBottom: 4 }}>Trap</div>
          <div className="filter-row" style={{ marginBottom: 0 }}>
            {trapOptions.map(id => (
              <button key={id} className={`chip${trapFilter === id ? ' active' : ''}`} onClick={() => setTrapFilter(id)}>
                {id}
              </button>
            ))}
          </div>
        </div>
        <div>
          <div style={{ fontSize: '0.65rem', color: '#94a3b8', textTransform: 'uppercase', marginBottom: 4 }}>Type</div>
          <div className="filter-row" style={{ marginBottom: 0 }}>
            {typeOptions.map(t => (
              <button key={t} className={`chip${typeFilter === t ? ' active' : ''}`} onClick={() => setTypeFilter(t)}>
                {t === 'All' ? 'All' : `${EVENT_STYLES[t]?.icon} ${t}`}
              </button>
            ))}
          </div>
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="empty-state">No events matching filters</div>
      ) : (
        <div className="card" style={{ padding: '4px 0' }}>
          {filtered.map((e, i) => {
            const sty = EVENT_STYLES[e.type] || { color: '#94a3b8', icon: '•' };
            return (
              <div key={i} style={{
                display: 'flex', gap: 14, padding: '12px 16px',
                borderBottom: i < filtered.length - 1 ? '1px solid rgba(56,189,248,0.1)' : 'none',
                alignItems: 'flex-start',
              }}>
                <div style={{
                  width: 32, height: 32, borderRadius: '50%', background: `${sty.color}22`,
                  border: `1px solid ${sty.color}44`, display: 'flex', alignItems: 'center',
                  justifyContent: 'center', fontSize: '0.9rem', flexShrink: 0
                }}>
                  {sty.icon}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                    <div style={{ fontSize: '0.85rem', color: '#f0f9ff' }}>{e.event}</div>
                    <div style={{ fontSize: '0.7rem', color: '#94a3b8', whiteSpace: 'nowrap' }}>{timeAgo(e.time)}</div>
                  </div>
                  <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginTop: 2 }}>
                    {e.trapName} · <span style={{ fontFamily: 'monospace' }}>{e.trapId}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
