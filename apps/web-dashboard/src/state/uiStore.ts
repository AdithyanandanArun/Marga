import { create } from 'zustand';

export type ViewMode = 'control-center' | 'driver' | 'scenario' | 'replay';
export type PanelId = 'alerts' | 'inspector' | 'health' | 'controls' | 'layers';

interface Viewport {
  latitude: number;
  longitude: number;
  zoom: number;
  bearing: number;
  pitch: number;
}

interface UIState {
  viewMode: ViewMode;
  viewport: Viewport;
  selectedEntityId: string | null;
  selectedEntityType: string | null;
  openPanels: Set<PanelId>;
  isSimulationMode: boolean;
  showUncertainty: boolean;
  showTrajectories: boolean;
  showRiskZones: boolean;
  showV2XLinks: boolean;
  showSignals: boolean;
  showHazards: boolean;
  showRoadEvents: boolean;
  showRSUs: boolean;
  followActorId: string | null;
  darkMapStyle: boolean;

  setViewMode: (mode: ViewMode) => void;
  setViewport: (viewport: Partial<Viewport>) => void;
  selectEntity: (id: string | null, type: string | null) => void;
  togglePanel: (panel: PanelId) => void;
  setSimulationMode: (on: boolean) => void;
  toggleLayer: (layer: LayerToggle) => void;
  followActor: (actorId: string | null) => void;
  setDarkMap: (dark: boolean) => void;
}

type LayerToggle =
  | 'uncertainty' | 'trajectories' | 'riskZones'
  | 'v2xLinks' | 'signals' | 'hazards' | 'roadEvents' | 'rsus';

const DEFAULT_VIEWPORT: Viewport = {
  latitude: 12.9716,
  longitude: 77.5946,
  zoom: 13,
  bearing: 0,
  pitch: 45,
};

export const useUIStore = create<UIState>((set) => ({
  viewMode: 'control-center',
  viewport: DEFAULT_VIEWPORT,
  selectedEntityId: null,
  selectedEntityType: null,
  openPanels: new Set<PanelId>(['alerts', 'health']),
  isSimulationMode: true,
  // Positional uncertainty is useful supporting evidence, but drawing a ring
  // around every moving road user makes the live scene look as if actors are
  // blinking. Keep it available under Advanced → Layers instead.
  showUncertainty: false,
  showTrajectories: false,
  showRiskZones: true,
  showV2XLinks: false,
  showSignals: true,
  showHazards: true,
  showRoadEvents: true,
  showRSUs: true,
  followActorId: null,
  darkMapStyle: true,

  setViewMode: (viewMode) => set({ viewMode }),

  setViewport: (partial) =>
    set((state) => ({ viewport: { ...state.viewport, ...partial } })),

  selectEntity: (id, type) =>
    set({
      selectedEntityId: id,
      selectedEntityType: type,
      openPanels: id
        ? new Set([...Array.from(useUIStore.getState().openPanels), 'inspector' as PanelId])
        : useUIStore.getState().openPanels,
    }),

  togglePanel: (panel) =>
    set((state) => {
      const next = new Set(state.openPanels);
      if (next.has(panel)) next.delete(panel);
      else next.add(panel);
      return { openPanels: next };
    }),

  setSimulationMode: (isSimulationMode) => set({ isSimulationMode }),

  toggleLayer: (layer) =>
    set((state) => {
      const map: Record<LayerToggle, keyof UIState> = {
        uncertainty: 'showUncertainty',
        trajectories: 'showTrajectories',
        riskZones: 'showRiskZones',
        v2xLinks: 'showV2XLinks',
        signals: 'showSignals',
        hazards: 'showHazards',
        roadEvents: 'showRoadEvents',
        rsus: 'showRSUs',
      };
      const key = map[layer];
      return { [key]: !state[key] } as Partial<UIState>;
    }),

  followActor: (followActorId) => set({ followActorId }),
  setDarkMap: (darkMapStyle) => set({ darkMapStyle }),
}));
