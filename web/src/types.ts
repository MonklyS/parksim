// Shapes shared with the Python API (see parksim_server/models.py and parksim/view.py).

export type Category = "ride" | "show" | "food" | "restroom" | "shop" | "first_aid";
export type Tile = [number, number];

export interface Template {
  key: string;
  label: string;
  category: Category;
  description: string;
  size: [number, number];
  build_cost: number;
  stats: Record<string, unknown>;
}

export interface AttractionSpec {
  id: string;
  template: string;
  name?: string;
  x: number;
  y: number;
  w?: number;
  h?: number;
  entrance: Tile;
}

export interface Layout {
  name: string;
  width: number;
  height: number;
  entrance: Tile;
  paths: [number, number, number, number][];
  attractions: AttractionSpec[];
}

export interface Validation {
  ok: boolean;
  error: string | null;
  warnings: string[];
}

export interface SimSettings {
  seed?: number;
  attendance: number;
  storm_chance: number;
  high_temp_f: number;
  breakdowns: boolean;
  incidents: boolean;
}

export interface AttractionFrame {
  id: string;
  status: string;
  posted?: number | null;
  queue?: number;
  next_show?: number | null;
  seated?: number;
  wait?: number;
}

export interface LogEntry {
  time: number;
  kind: string;
  severity: "info" | "warning" | "critical";
  message: string;
  attraction: string | null;
}

export interface InitMessage {
  type: "init";
  seed: number;
  attendance: number;
  open_time: number;
  close_time: number;
  end_time: number;
  speeds: number[];
  speed: number;
}

export interface Frame {
  type: "frame";
  time: number;
  clock: string;
  weather: "clear" | "cloudy" | "rain" | "thunderstorm";
  temperature_f: number;
  in_park: number;
  arrivals: number;
  avg_happiness: number | null;
  open: boolean;
  attractions: AttractionFrame[];
  /** Flat list: [id, x*10, y*10, mood 0-9, ...] */
  guests: number[];
  log: LogEntry[];
  speed: number;
  paused: boolean;
  errors: string[];
}

export interface RideReport {
  id: string;
  name: string;
  riders: number;
  capacity_per_hour: number;
  avg_wait_min: number;
  max_wait_min: number;
  peak_posted_min: number;
  posted_bias_min: number;
  uptime_pct: number;
}

export interface DayReport {
  park: string;
  seed: number;
  summary: {
    attendance: number;
    peak_in_park: number;
    avg_exit_happiness: number;
    rides_per_guest: number;
    revenue: number;
    revenue_per_guest: number;
    incidents: Record<string, number>;
    guests_treated: number;
    unserved_medical: number;
  };
  rides: RideReport[];
  thoughts: Record<string, number>;
  exit_reasons: Record<string, number>;
  [key: string]: unknown;
}
