import { writable } from 'svelte/store';

export interface CrossSectionData {
  distance_km: number[];
  height_km: number[][]; // per-sweep heights
  values: number[][];    // per-sweep values
  variable: string;
  units: string;
  n_sweeps: number;
}

export interface VerticalProfileData {
  heights_km: number[];
  values: number[];
  elevations_deg: number[];
  variable: string;
  units: string;
}

export interface CrossSectionLinePoints {
  startX: number; // NDC or pixel coords on PPI
  startY: number;
  endX: number;
  endY: number;
  startLat: number;
  startLon: number;
  endLat: number;
  endLon: number;
}

export interface CrossSectionState {
  /** Whether the cross-section line drawing tool is active */
  lineToolActive: boolean;
  /** Whether the vertical profile click-to-probe mode is active */
  probeToolActive: boolean;
  /** The drawn line on the PPI (null if not drawn) */
  line: CrossSectionLinePoints | null;
  /** Cross-section result data */
  crossSectionData: CrossSectionData | null;
  /** Vertical profile result data (multiple variables can be stacked) */
  verticalProfiles: VerticalProfileData[];
  /** Whether a request is in progress */
  isLoading: boolean;
  /** Active tab: 'cross-section' or 'vertical-profile' */
  activeTab: 'cross-section' | 'vertical-profile';
}

export const crossSectionStore = writable<CrossSectionState>({
  lineToolActive: false,
  probeToolActive: false,
  line: null,
  crossSectionData: null,
  verticalProfiles: [],
  isLoading: false,
  activeTab: 'cross-section',
});
