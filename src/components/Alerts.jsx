import { useState } from 'react';
import { computeStatus, STATUS_META, STATUSES } from '../data/traps';

function timeAgo(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function buildAlerts(traps) {
  const alerts = [];
  traps.forEach(trap => {
    const status = computeStatus(trap);

    if (status === STATUSES.THEFT) {
      alerts.push({
        id: `${trap.id}-theft`, trapId: trap.id, trapName: trap.name,
        icon: '🚨', title: 'Possible Theft / Dragged Away',
        desc: `GPS deviation ${(trap.gpsDeviation * 1000).toFixed(0)}m from home with high movement detected.`,
        time: trap.lastActivity, severity: 'critical', unread: true,
      });
    }
    if (status === STATUSES.LIKELY_CATCH) {
      alerts.push({
        id: `${trap.id}-catch`, trapId: trap.id, trapName: trap.name,
        icon: '🦀', title: 'Likely Catch Detected',
        desc: `Weight +${trap.weightChange.toFixed(1)} kg and ${trap.baitDisturbance}% bait disturbance.`,
        time: trap.lastActivity, severity: 'success', unread: true,
      });
    }
    if (status === STATUSES.ACTIVITY) {
      alerts.push({
        id: `${trap.id}-activity`, trapId: trap.id, trapName: trap.name,
        icon: '📡', title: 'Trap Activity Detected',
        desc: `Movement ${trap.movement.toFixed(1)}, vibration ${trap.vibration.toFixed(1)}.`,
        time: trap.lastActivity, severity: 'info', unread: false,
      });
    }
    if (status === STATUSES.NEEDS_CHECK) {
      const hoursAgoVal = (Date.now() - new Date(trap.lastActivity).getTime()) / 36e5;
      alerts.push({
        id: `${trap.id}-check`, trapId: trap.id, trapName: trap.name,
        icon: '⏰', title: 'Trap Needs Checking',
        desc: `No activity for ${Math.floor(hoursAgoVal)} hours.`,
        time: trap.lastChecked, severity: 'warning', unread: false,
      });
    }
    if (trap.battery <= 20) {
      alerts.push({
        id: `${trap.id}-batt`, trapId: trap.id, trapName: trap.name,
        icon: '🔋', title: 'Low Battery Warning',
        desc: `Battery at ${trap.battery}% — replace soon.`,
        time: trap.lastActivity, severity: 'warning', unread: trap.battery <= 15,
      });
    }
  });

  return alerts.sort((a, b) => {
    const order = { critical: 0, success: 1, warning: 2, info: 3 };
    return (order[a.severity] ?? 9) - (order[b.severity] ?? 9);
  });
}

const SEVERITY_STYLES = {
  critical: { bg: '#fee2e2', color: '#dc2626', label: 'Critical' },
  success:  { bg: '#dcfce7', color: '#16a34a', label: 'Catch' },
  warning:  { bg: '#fef3c7', color: '#d97706', label: 'Warning' },
  info:     { bg: '#e0f2fe', color: '#0284c7', label: 'Info' },
};

export default function Alerts({ traps }) {
  const [dismissed, setDismissed] = useState(new Set());
  const alerts = buildAlerts(traps).filter(a => !dismissed.has(a.id));
  const unreadCount = alerts.filter(a => a.unread).length;

  return (
    <div>
      <div className="section-header" style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="section-title">Alerts & Notifications</span>
          {unreadCount > 0 && (
            <span style={{ background: '#dc2626', color: 'white', fontSize: '0.7rem', padding: '2px 8px', borderRadius: 10, fontWeight: 700 }}>
              {unreadCount} new
            </span>
          )}
        </div>
        {alerts.length > 0 && (
          <button
            className="chip"
            onClick={() => setDismissed(new Set(alerts.map(a => a.id)))}
          >
            Clear all
          </button>
        )}
      </div>

      {alerts.length === 0 ? (
        <div className="empty-state">
          <div style={{ fontSize: '2.5rem', marginBottom: 10 }}>✅</div>
          All clear — no active alerts
        </div>
      ) : (
        <div className="alerts-list">
          {alerts.map(alert => {
            const sty = SEVERITY_STYLES[alert.severity];
            return (
              <div key={alert.id} className={`alert-item${alert.unread ? ' unread' : ''}`}>
                <div className="alert-icon">{alert.icon}</div>
                <div className="alert-body" style={{ flex: 1 }}>
                  <div className="alert-title">{alert.title}</div>
                  <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginBottom: 2 }}>{alert.trapName} · {alert.trapId}</div>
                  <div className="alert-desc">{alert.desc}</div>
                  <div className="alert-time">{timeAgo(alert.time)}</div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                  <span className="alert-badge" style={{ background: sty.bg, color: sty.color }}>{sty.label}</span>
                  <button
                    className="chip"
                    style={{ padding: '2px 8px', fontSize: '0.65rem' }}
                    onClick={() => setDismissed(prev => new Set([...prev, alert.id]))}
                  >Dismiss</button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
