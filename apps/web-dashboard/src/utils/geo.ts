export function mpsToKmh(mps: number): number {
  return mps * 3.6;
}

export function headingToCardinal(deg: number): string {
  const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  return dirs[Math.round(((deg % 360 + 360) % 360) / 45) % 8];
}

export function formatCoord(lat: number, lon: number): string {
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

export function formatDistance(meters: number): string {
  if (meters < 1000) return `${Math.round(meters)} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}

export function formatSpeed(mps: number): string {
  return `${mpsToKmh(mps).toFixed(0)} km/h`;
}

export function confidenceLabel(c: number): string {
  if (c >= 0.8) return 'High';
  if (c >= 0.5) return 'Medium';
  return 'Low';
}

export function severityColor(severity: number): string {
  if (severity >= 0.8) return 'var(--severity-critical)';
  if (severity >= 0.6) return 'var(--severity-high)';
  if (severity >= 0.4) return 'var(--severity-medium)';
  return 'var(--severity-low)';
}

export function severityLabel(severity: string): string {
  return severity.charAt(0) + severity.slice(1).toLowerCase();
}

export function timeAgo(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  if (diff < 1000) return 'just now';
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  return `${Math.floor(diff / 3600000)}h ago`;
}

export function actorTypeIcon(type: string): string {
  const icons: Record<string, string> = {
    CAR: '\u{1F697}',
    BIKE: '\u{1F6B2}',
    AUTO: '\u{1F6FA}',
    BUS: '\u{1F68C}',
    TRUCK: '\u{1F69A}',
    AMBULANCE: '\u{1F691}',
    OTHER: '\u{1F698}',
  };
  return icons[type] ?? '\u{1F698}';
}
