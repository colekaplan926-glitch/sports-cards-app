export const STATUSES = {
  EMPTY: 'Empty',
  ACTIVITY: 'Activity Detected',
  LIKELY_CATCH: 'Likely Catch',
  NEEDS_CHECK: 'Needs Check',
  THEFT: 'Possible Theft/Dragged Away',
};

export const STATUS_META = {
  [STATUSES.EMPTY]: { color: '#64748b', bg: '#f1f5f9', icon: '⬜', priority: 0 },
  [STATUSES.ACTIVITY]: { color: '#0284c7', bg: '#e0f2fe', icon: '🔵', priority: 2 },
  [STATUSES.LIKELY_CATCH]: { color: '#16a34a', bg: '#dcfce7', icon: '🟢', priority: 3 },
  [STATUSES.NEEDS_CHECK]: { color: '#d97706', bg: '#fef3c7', icon: '🟡', priority: 1 },
  [STATUSES.THEFT]: { color: '#dc2626', bg: '#fee2e2', icon: '🔴', priority: 4 },
};

export function computeStatus(trap) {
  const hoursSince = (Date.now() - new Date(trap.lastActivity).getTime()) / 36e5;
  const movedFar = trap.gpsDeviation > 0.05; // km from home

  if (movedFar && trap.movement > 7) return STATUSES.THEFT;
  if (trap.weightChange > 1.5 && trap.baitDisturbance > 60) return STATUSES.LIKELY_CATCH;
  if (trap.movement > 3 || trap.vibration > 5 || trap.baitDisturbance > 30) return STATUSES.ACTIVITY;
  if (hoursSince > 48) return STATUSES.NEEDS_CHECK;
  return STATUSES.EMPTY;
}

function hoursAgo(h) {
  return new Date(Date.now() - h * 36e5).toISOString();
}

export const DEMO_TRAPS = [
  {
    id: 'CT-001', name: 'Blue Point Reef',
    lat: 44.6521, lng: -63.5780,
    homeLat: 44.6521, homeLng: -63.5780,
    depth: 18, battery: 87,
    lastChecked: hoursAgo(6),
    lastActivity: hoursAgo(2),
    movement: 4.2, vibration: 6.1, baitDisturbance: 72, weightChange: 2.3, gpsDeviation: 0.01,
    history: [
      { time: hoursAgo(2), event: 'Weight spike +2.3 kg', type: 'catch' },
      { time: hoursAgo(2.1), event: 'Bait disturbance 72%', type: 'activity' },
      { time: hoursAgo(6), event: 'Trap checked by user', type: 'check' },
    ],
  },
  {
    id: 'CT-002', name: 'Sandy Neck Shoal',
    lat: 44.6610, lng: -63.5540,
    homeLat: 44.6610, homeLng: -63.5540,
    depth: 12, battery: 62,
    lastChecked: hoursAgo(24),
    lastActivity: hoursAgo(55),
    movement: 0.3, vibration: 0.8, baitDisturbance: 5, weightChange: 0.1, gpsDeviation: 0.008,
    history: [
      { time: hoursAgo(24), event: 'Trap checked by user', type: 'check' },
      { time: hoursAgo(48), event: 'Trap set', type: 'set' },
    ],
  },
  {
    id: 'CT-003', name: 'North Marker Drop',
    lat: 44.6700, lng: -63.5420,
    homeLat: 44.6620, homeLng: -63.5400,
    depth: 9, battery: 34,
    lastChecked: hoursAgo(18),
    lastActivity: hoursAgo(0.5),
    movement: 9.8, vibration: 11.2, baitDisturbance: 12, weightChange: 0.2, gpsDeviation: 0.092,
    history: [
      { time: hoursAgo(0.5), event: 'GPS deviation 92m detected', type: 'alert' },
      { time: hoursAgo(0.5), event: 'High movement 9.8', type: 'alert' },
      { time: hoursAgo(18), event: 'Trap checked by user', type: 'check' },
    ],
  },
  {
    id: 'CT-004', name: 'Harbor Mouth West',
    lat: 44.6450, lng: -63.5650,
    homeLat: 44.6450, homeLng: -63.5650,
    depth: 22, battery: 91,
    lastChecked: hoursAgo(4),
    lastActivity: hoursAgo(1),
    movement: 3.5, vibration: 4.8, baitDisturbance: 38, weightChange: 0.8, gpsDeviation: 0.005,
    history: [
      { time: hoursAgo(1), event: 'Vibration spike detected', type: 'activity' },
      { time: hoursAgo(3), event: 'Bait disturbance 38%', type: 'activity' },
      { time: hoursAgo(4), event: 'Trap checked by user', type: 'check' },
    ],
  },
  {
    id: 'CT-005', name: 'Kelp Bed South',
    lat: 44.6390, lng: -63.5720,
    homeLat: 44.6390, homeLng: -63.5720,
    depth: 15, battery: 15,
    lastChecked: hoursAgo(72),
    lastActivity: hoursAgo(70),
    movement: 0.5, vibration: 1.2, baitDisturbance: 8, weightChange: 0.0, gpsDeviation: 0.003,
    history: [
      { time: hoursAgo(70), event: 'Low battery warning 15%', type: 'alert' },
      { time: hoursAgo(72), event: 'Trap checked by user', type: 'check' },
    ],
  },
  {
    id: 'CT-006', name: 'Outer Bank Line',
    lat: 44.6580, lng: -63.5900,
    homeLat: 44.6580, homeLng: -63.5900,
    depth: 28, battery: 78,
    lastChecked: hoursAgo(8),
    lastActivity: hoursAgo(3),
    movement: 5.1, vibration: 7.4, baitDisturbance: 81, weightChange: 3.1, gpsDeviation: 0.012,
    history: [
      { time: hoursAgo(3), event: 'Weight spike +3.1 kg', type: 'catch' },
      { time: hoursAgo(3.2), event: 'Bait disturbance 81%', type: 'activity' },
      { time: hoursAgo(8), event: 'Trap checked by user', type: 'check' },
    ],
  },
];
