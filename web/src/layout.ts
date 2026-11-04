import type { AttractionSpec, Layout, Template, Tile } from "./types";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface PlacementCheck {
  ok: boolean;
  rect: Rect;
  reason?: string;
  entrance?: Tile;
}

/**
 * The park being edited, as a tile grid. Mirrors the server's layout rules so the editor
 * can give instant feedback; the server still validates authoritatively on save.
 */
export class EditableLayout {
  name: string;
  readonly width: number;
  readonly height: number;
  entrance: Tile;
  buildings: AttractionSpec[] = [];
  private path: Uint8Array;
  private owners: (string | null)[];

  constructor(name: string, width: number, height: number, entrance: Tile) {
    this.name = name;
    this.width = width;
    this.height = height;
    this.entrance = entrance;
    this.path = new Uint8Array(width * height);
    this.owners = new Array(width * height).fill(null);
  }

  static blank(): EditableLayout {
    const layout = new EditableLayout("My Park", 48, 32, [24, 31]);
    for (let y = 25; y <= 31; y++) layout.path[layout.index(24, y)] = 1;
    return layout;
  }

  static from(data: Layout, catalog: Map<string, Template>): EditableLayout {
    const layout = new EditableLayout(data.name, data.width, data.height, data.entrance);
    for (const [x1, y1, x2, y2] of data.paths) {
      for (let x = Math.min(x1, x2); x <= Math.max(x1, x2); x++)
        for (let y = Math.min(y1, y2); y <= Math.max(y1, y2); y++)
          if (layout.inBounds(x, y)) layout.path[layout.index(x, y)] = 1;
    }
    layout.buildings = data.attractions.map((a) => ({ ...a, entrance: [...a.entrance] as Tile }));
    layout.rebuildOwners(catalog);
    return layout;
  }

  index(x: number, y: number) {
    return y * this.width + x;
  }

  inBounds(x: number, y: number) {
    return x >= 0 && y >= 0 && x < this.width && y < this.height;
  }

  isPath(x: number, y: number) {
    return this.inBounds(x, y) && this.path[this.index(x, y)] === 1;
  }

  ownerAt(x: number, y: number): string | null {
    return this.inBounds(x, y) ? this.owners[this.index(x, y)] : null;
  }

  building(id: string) {
    return this.buildings.find((b) => b.id === id);
  }

  sizeOf(spec: AttractionSpec, catalog: Map<string, Template>): [number, number] {
    const size = catalog.get(spec.template)?.size ?? [1, 1];
    return [spec.w ?? size[0], spec.h ?? size[1]];
  }

  rectOf(spec: AttractionSpec, catalog: Map<string, Template>): Rect {
    const [w, h] = this.sizeOf(spec, catalog);
    return { x: spec.x, y: spec.y, w, h };
  }

  rebuildOwners(catalog: Map<string, Template>) {
    this.owners.fill(null);
    for (const b of this.buildings) {
      const r = this.rectOf(b, catalog);
      for (let x = r.x; x < r.x + r.w; x++)
        for (let y = r.y; y < r.y + r.h; y++)
          if (this.inBounds(x, y)) this.owners[this.index(x, y)] = b.id;
    }
  }

  /** Paint or erase one path tile. Returns true if anything changed. */
  setPath(x: number, y: number, on: boolean): boolean {
    if (!this.inBounds(x, y)) return false;
    const i = this.index(x, y);
    if (on && this.owners[i] !== null) return false;
    if (!on && x === this.entrance[0] && y === this.entrance[1]) return false;
    if ((this.path[i] === 1) === on) return false;
    this.path[i] = on ? 1 : 0;
    return true;
  }

  setGate(x: number, y: number): boolean {
    if (!this.isPath(x, y)) return false;
    this.entrance = [x, y];
    return true;
  }

  /** Best path tile touching a rectangle's edge, nearest the middle of the building. */
  findEntrance(r: Rect): Tile | undefined {
    const cx = r.x + r.w / 2 - 0.5;
    const cy = r.y + r.h / 2 - 0.5;
    const candidates: Tile[] = [];
    for (let x = r.x; x < r.x + r.w; x++) candidates.push([x, r.y + r.h], [x, r.y - 1]);
    for (let y = r.y; y < r.y + r.h; y++) candidates.push([r.x - 1, y], [r.x + r.w, y]);
    return candidates
      .filter(([x, y]) => this.isPath(x, y))
      .sort((a, b) => Math.hypot(a[0] - cx, a[1] - cy) - Math.hypot(b[0] - cx, b[1] - cy))[0];
  }

  checkPlacement(template: Template, x: number, y: number, ignoreId?: string): PlacementCheck {
    const [w, h] = template.size;
    const rect = { x, y, w, h };
    for (let tx = x; tx < x + w; tx++) {
      for (let ty = y; ty < y + h; ty++) {
        if (!this.inBounds(tx, ty)) return { ok: false, rect, reason: "Outside the park" };
        if (this.isPath(tx, ty)) return { ok: false, rect, reason: "Overlaps a path" };
        const owner = this.ownerAt(tx, ty);
        if (owner !== null && owner !== ignoreId)
          return { ok: false, rect, reason: "Overlaps another building" };
      }
    }
    const entrance = this.findEntrance(rect);
    if (!entrance) return { ok: false, rect, reason: "Needs a path touching one side" };
    return { ok: true, rect, entrance };
  }

  place(template: Template, x: number, y: number, catalog: Map<string, Template>) {
    const check = this.checkPlacement(template, x, y);
    if (!check.ok || !check.entrance) return null;
    const spec: AttractionSpec = {
      id: this.uniqueId(template.key),
      template: template.key,
      name: this.uniqueName(template.label),
      x,
      y,
      entrance: check.entrance,
    };
    this.buildings.push(spec);
    this.rebuildOwners(catalog);
    return spec;
  }

  remove(id: string, catalog: Map<string, Template>) {
    this.buildings = this.buildings.filter((b) => b.id !== id);
    this.rebuildOwners(catalog);
  }

  /** After paths change, re-attach entrances that lost their path. Returns ids still broken. */
  repairEntrances(catalog: Map<string, Template>): Set<string> {
    const broken = new Set<string>();
    for (const b of this.buildings) {
      if (this.isPath(...b.entrance)) continue;
      const fixed = this.findEntrance(this.rectOf(b, catalog));
      if (fixed) b.entrance = fixed;
      else broken.add(b.id);
    }
    return broken;
  }

  toLayout(): Layout {
    const paths: Layout["paths"] = [];
    for (let y = 0; y < this.height; y++) {
      let start = -1;
      for (let x = 0; x <= this.width; x++) {
        const on = x < this.width && this.isPath(x, y);
        if (on && start < 0) start = x;
        if (!on && start >= 0) {
          paths.push([start, y, x - 1, y]);
          start = -1;
        }
      }
    }
    return {
      name: this.name.trim() || "Untitled Park",
      width: this.width,
      height: this.height,
      entrance: this.entrance,
      paths,
      attractions: this.buildings.map((b) => ({ ...b })),
    };
  }

  private uniqueId(base: string) {
    const ids = new Set(this.buildings.map((b) => b.id));
    for (let n = 1; ; n++) if (!ids.has(`${base}_${n}`)) return `${base}_${n}`;
  }

  private uniqueName(label: string) {
    const names = new Set(this.buildings.map((b) => b.name));
    if (!names.has(label)) return label;
    for (let n = 2; ; n++) if (!names.has(`${label} ${n}`)) return `${label} ${n}`;
  }
}
