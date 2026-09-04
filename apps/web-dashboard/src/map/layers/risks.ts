import { ScatterplotLayer, LineLayer, TextLayer } from '@deck.gl/layers';
import type { RiskEvent, VehicleState } from '../../types/canonical';

const RISK_COLORS: Record<string, [number, number, number, number]> = {
  HIGH: [239, 68, 68, 150],
  MEDIUM: [234, 179, 8, 120],
  LOW: [59, 130, 246, 80],
};

function riskColor(severity: number): [number, number, number, number] {
  if (severity >= 0.7) return RISK_COLORS.HIGH;
  if (severity >= 0.4) return RISK_COLORS.MEDIUM;
  return RISK_COLORS.LOW;
}

interface RiskLine {
  risk_id: string;
  source: [number, number];
  target: [number, number];
  severity: number;
  ttc: number;
}

export function createRiskLayer(
  risks: RiskEvent[],
  vehicles: Map<string, VehicleState>,
  zoom: number,
) {
  if (zoom < 12 || risks.length === 0) return [];

  const lines: RiskLine[] = [];
  const midpoints: { risk_id: string; position: [number, number]; severity: number; ttc: number }[] = [];

  // The overview tells one safety story. Detailed multi-risk inspection is
  // intentionally kept in the advanced panel instead of drawing a web.
  const primaryRisk = [...risks].sort(
    (a, b) => b.risk_score - a.risk_score || a.time_to_conflict_s - b.time_to_conflict_s,
  )[0];
  for (const risk of primaryRisk ? [primaryRisk] : []) {
    if (risk.affected_actor_ids.length < 2) continue;
    const a = vehicles.get(risk.affected_actor_ids[0]);
    const b = vehicles.get(risk.affected_actor_ids[1]);
    if (!a || !b) continue;

    const source: [number, number] = [a.position.lon, a.position.lat];
    const target: [number, number] = [b.position.lon, b.position.lat];

    lines.push({
      risk_id: risk.risk_id,
      source,
      target,
      severity: risk.severity,
      ttc: risk.time_to_conflict_s,
    });

    midpoints.push({
      risk_id: risk.risk_id,
      position: [(source[0] + target[0]) / 2, (source[1] + target[1]) / 2],
      severity: risk.severity,
      ttc: risk.time_to_conflict_s,
    });
  }

  const layers = [];

  if (lines.length > 0) {
    layers.push(
      new LineLayer({
        id: 'risk-lines',
        data: lines,
        getSourcePosition: (d: RiskLine) => d.source,
        getTargetPosition: (d: RiskLine) => d.target,
        getColor: (d: RiskLine) => riskColor(d.severity),
        getWidth: (d: RiskLine) => (d.severity >= 0.7 ? 3 : 2),
        widthUnits: 'pixels',
        pickable: true,
      }),
    );
  }

  if (midpoints.length > 0 && zoom > 14) {
    layers.push(
      new ScatterplotLayer({
        id: 'risk-midpoints',
        data: midpoints,
        getPosition: (d: { position: [number, number] }) => d.position,
        getRadius: 11,
        getFillColor: (d: { severity: number }) => riskColor(d.severity),
        radiusUnits: 'pixels',
        pickable: false,
      }),
    );
    layers.push(
      new TextLayer({
        id: 'risk-conflict-label',
        data: midpoints,
        getPosition: (d: { position: [number, number] }) => d.position,
        getText: (d: { ttc: number }) => `CONFLICT · ${d.ttc.toFixed(1)}s`,
        getColor: [255, 255, 255, 235],
        getSize: 11,
        sizeUnits: 'pixels',
        getTextAnchor: 'middle',
        getAlignmentBaseline: 'bottom',
        getPixelOffset: [0, -13],
        billboard: true,
        pickable: false,
      }),
    );
  }

  return layers;
}
