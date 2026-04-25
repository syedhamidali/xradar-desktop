import { writable } from 'svelte/store';

export interface CellData {
  id: string;
  centroid_az: number;
  centroid_range: number;
  area_km2: number;
  max_dbz: number;
  mean_dbz: number;
  boundary_points: [number, number][];
}

export interface TrackData {
  track_id: string;
  cell_history: CellData[];
  speed_mps: number;
  direction_deg: number;
}

export interface StormCellsState {
  cells: CellData[];
  tracks: TrackData[];
  selectedCellId: string | null;
  overlayVisible: boolean;
  isIdentifying: boolean;
}

export const stormCells = writable<StormCellsState>({
  cells: [],
  tracks: [],
  selectedCellId: null,
  overlayVisible: true,
  isIdentifying: false,
});
