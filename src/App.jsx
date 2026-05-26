import { useState, useEffect } from 'react';
import { DEMO_TRAPS, computeStatus, STATUSES } from './data/traps';
import Dashboard from './components/Dashboard';
import MapView from './components/MapView';
import Alerts from './components/Alerts';
import History from './components/History';
import 'leaflet/dist/leaflet.css';
import './index.css';

const TABS = [
  { id: 'dashboard', label: 'Dashboard', icon: '📊' },
  { id: 'map',       label: 'Map',       icon: '🗺️' },
  { id: 'alerts',    label: 'Alerts',    icon: '🔔' },
  { id: 'history',   label: 'History',   icon: '📋' },
];

function useAlertCount(traps) {
  return traps.filter(t => [STATUSES.THEFT, STATUSES.LIKELY_CATCH].includes(computeStatus(t))).length;
}

export default function App() {
  const [tab, setTab] = useState('dashboard');
  const [traps] = useState(DEMO_TRAPS);
  const [now, setNow] = useState(Date.now());
  const alertCount = useAlertCount(traps);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);

  const lastUpdate = new Date(now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  return (
    <div className="app">
      <header className="header">
        <div className="header-logo">
          <span>🦀</span>
          <h1>CrabWatch</h1>
        </div>
        <div className="header-status">
          <strong>{traps.length}</strong> traps · Updated {lastUpdate}
          {alertCount > 0 && (
            <span style={{
              marginLeft: 10, background: '#dc2626', color: 'white',
              fontSize: '0.7rem', padding: '2px 8px', borderRadius: 10, fontWeight: 700
            }}>
              {alertCount} alert{alertCount !== 1 ? 's' : ''}
            </span>
          )}
        </div>
      </header>

      <nav className="nav">
        {TABS.map(t => (
          <button
            key={t.id}
            className={`nav-btn${tab === t.id ? ' active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.icon} {t.label}
            {t.id === 'alerts' && alertCount > 0 && (
              <span style={{
                background: '#dc2626', color: 'white', fontSize: '0.65rem',
                padding: '1px 6px', borderRadius: 8, fontWeight: 700
              }}>{alertCount}</span>
            )}
          </button>
        ))}
      </nav>

      <main className="main">
        {tab === 'dashboard' && <Dashboard traps={traps} />}
        {tab === 'map'       && <MapView   traps={traps} />}
        {tab === 'alerts'    && <Alerts    traps={traps} />}
        {tab === 'history'   && <History   traps={traps} />}
      </main>

      <footer style={{
        textAlign: 'center', padding: '12px', fontSize: '0.7rem',
        color: '#475569', borderTop: '1px solid rgba(56,189,248,0.1)'
      }}>
        CrabWatch · Demo Mode · Sensor data simulated
      </footer>
    </div>
  );
}
