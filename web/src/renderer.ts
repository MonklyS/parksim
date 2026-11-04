import { Application, Container, Graphics, Sprite, Text, Texture } from "pixi.js";
import type { EditableLayout, Rect } from "./layout";
import type { AttractionFrame, Category, Template, Tile } from "./types";

export const TILE = 24;

export const CATEGORY_COLOR: Record<Category, number> = {
  ride: 0xe2603f,
  show: 0x8b5cc4,
  food: 0xe9a23b,
  restroom: 0x3b8fd9,
  shop: 0xd9478f,
  first_aid: 0xf4f1ec,
};

export const CATEGORY_ICON: Record<Category, string> = {
  ride: "🎢",
  show: "🎭",
  food: "🍔",
  restroom: "🚻",
  shop: "🛍️",
  first_aid: "⛑️",
};

const GRASS = 0x79ad62;
const PATH = 0xe2d3ae;
const PATH_EDGE = 0xc8b688;

// Mood 0..9 -> red, through amber, to green.
const MOOD_COLORS = Array.from({ length: 10 }, (_, i) => {
  const t = i / 9;
  const lerp = (a: number, b: number, k: number) => Math.round(a + (b - a) * k);
  const [r, g, b] =
    t < 0.5
      ? [lerp(0xd6, 0xf2, t * 2), lerp(0x45, 0xc1, t * 2), lerp(0x41, 0x3c, t * 2)]
      : [lerp(0xf2, 0x2e, (t - 0.5) * 2), lerp(0xc1, 0xc8, (t - 0.5) * 2), lerp(0x3c, 0x62, (t - 0.5) * 2)];
  return (r << 16) | (g << 8) | b;
});

const STATUS_LABEL: Record<string, string> = {
  breakdown: "DOWN",
  inspection: "INSPECT",
  weather: "⚡ HOLD",
  manual: "CLOSED",
  closed: "CLOSED",
};

interface GuestDot {
  sprite: Sprite;
  fx: number;
  fy: number;
  tx: number;
  ty: number;
}

interface Badge {
  box: Graphics;
  label: Text;
  key: string;
}

export interface DrawOptions {
  selected: string | null;
  broken: Set<string>;
}

export class ParkRenderer {
  readonly app = new Application();
  readonly world = new Container();
  private ground = new Graphics();
  private paths = new Graphics();
  private buildings = new Container();
  private overlay = new Graphics();
  private guestLayer = new Container();
  private badgeLayer = new Container();
  private dot: Texture = Texture.EMPTY;
  private dots = new Map<number, GuestDot>();
  private pool: Sprite[] = [];
  private frameAt = 0;
  private frameInterval = 500;
  private badges = new Map<string, Badge>();
  private bodies = new Map<string, Container>();
  private size = { w: 0, h: 0 };

  async init(host: HTMLElement) {
    await this.app.init({
      resizeTo: host,
      background: 0x5d8f4c,
      antialias: true,
      autoDensity: true,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
    });
    host.appendChild(this.app.canvas);
    this.world.addChild(
      this.ground,
      this.paths,
      this.buildings,
      this.guestLayer,
      this.overlay,
      this.badgeLayer,
    );
    this.app.stage.addChild(this.world);
    this.dot = this.app.renderer.generateTexture(new Graphics().circle(0, 0, 4).fill(0xffffff));
    this.app.ticker.add(() => this.animateGuests());
  }

  // ---- camera ----------------------------------------------------------------------------
  fit() {
    const { width, height } = this.app.screen;
    const worldW = this.size.w * TILE;
    const worldH = this.size.h * TILE;
    if (!worldW || !width) return;
    const scale = Math.min((width - 32) / worldW, (height - 32) / worldH, 2.5);
    this.world.scale.set(scale);
    this.world.position.set((width - worldW * scale) / 2, (height - worldH * scale) / 2);
  }

  zoomAt(clientX: number, clientY: number, factor: number) {
    const rect = this.app.canvas.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const old = this.world.scale.x;
    const next = Math.min(Math.max(old * factor, 0.25), 5);
    this.world.position.set(
      px - ((px - this.world.x) * next) / old,
      py - ((py - this.world.y) * next) / old,
    );
    this.world.scale.set(next);
  }

  panBy(dx: number, dy: number) {
    this.world.position.set(this.world.x + dx, this.world.y + dy);
  }

  toTile(clientX: number, clientY: number): Tile {
    const rect = this.app.canvas.getBoundingClientRect();
    const s = this.world.scale.x;
    return [
      Math.floor((clientX - rect.left - this.world.x) / s / TILE),
      Math.floor((clientY - rect.top - this.world.y) / s / TILE),
    ];
  }

  // ---- static layout ---------------------------------------------------------------------
  drawLayout(layout: EditableLayout, catalog: Map<string, Template>, opts: DrawOptions) {
    const first = this.size.w === 0;
    this.size = { w: layout.width, h: layout.height };

    const g = this.ground.clear();
    g.rect(0, 0, layout.width * TILE, layout.height * TILE).fill(GRASS);
    for (let x = 0; x <= layout.width; x++)
      g.moveTo(x * TILE, 0).lineTo(x * TILE, layout.height * TILE);
    for (let y = 0; y <= layout.height; y++)
      g.moveTo(0, y * TILE).lineTo(layout.width * TILE, y * TILE);
    g.stroke({ width: 1, color: 0x000000, alpha: 0.07 });

    const p = this.paths.clear();
    for (let y = 0; y < layout.height; y++)
      for (let x = 0; x < layout.width; x++)
        if (layout.isPath(x, y)) p.rect(x * TILE, y * TILE, TILE, TILE);
    p.fill(PATH).stroke({ width: 1, color: PATH_EDGE, alpha: 0.6 });
    // Park gate
    const [gx, gy] = layout.entrance;
    p.roundRect(gx * TILE + 2, gy * TILE + 2, TILE - 4, TILE - 4, 4)
      .fill(0xf5c542)
      .stroke({ width: 2, color: 0x8a6a10 });

    for (const child of this.buildings.removeChildren()) child.destroy({ children: true });
    this.bodies.clear();
    for (const b of layout.buildings) {
      const template = catalog.get(b.template);
      if (!template) continue;
      const r = layout.rectOf(b, catalog);
      const view = this.buildingView(r, template, b.name ?? template.label, {
        selected: opts.selected === b.id,
        broken: opts.broken.has(b.id),
      });
      // entrance marker
      const [ex, ey] = b.entrance;
      view.addChild(
        new Graphics()
          .roundRect(ex * TILE + 6, ey * TILE + 6, TILE - 12, TILE - 12, 3)
          .fill({ color: CATEGORY_COLOR[template.category], alpha: 0.9 })
          .stroke({ width: 1.5, color: 0x3a2f1a, alpha: 0.6 }),
      );
      this.buildings.addChild(view);
      this.bodies.set(b.id, view);
    }
    if (first) this.fit();
  }

  private buildingView(
    r: Rect,
    template: Template,
    name: string,
    flags: { selected: boolean; broken: boolean },
  ) {
    const view = new Container();
    const color = CATEGORY_COLOR[template.category];
    const body = new Graphics()
      .roundRect(r.x * TILE + 1.5, r.y * TILE + 1.5, r.w * TILE - 3, r.h * TILE - 3, 6)
      .fill(color)
      .stroke({
        width: flags.selected ? 3 : 1.5,
        color: flags.broken ? 0xff2020 : flags.selected ? 0xffffff : 0x2b2b2b,
        alpha: flags.selected || flags.broken ? 1 : 0.45,
      });
    view.addChild(body);
    const dark = template.category === "first_aid";
    const small = r.w * r.h <= 2;
    const label = new Text({
      text: small ? CATEGORY_ICON[template.category] : `${CATEGORY_ICON[template.category]}\n${name}`,
      style: {
        fontFamily: "Inter, system-ui, sans-serif",
        fontSize: small ? 13 : 11,
        fontWeight: "600",
        fill: dark ? 0x9b1c1c : 0xffffff,
        align: "center",
        wordWrap: true,
        wordWrapWidth: Math.max(r.w * TILE - 8, 20),
        lineHeight: small ? 14 : 13,
      },
      resolution: 3,
    });
    label.anchor.set(0.5);
    label.position.set((r.x + r.w / 2) * TILE, (r.y + r.h / 2) * TILE);
    view.addChild(label);
    return view;
  }

  // ---- editing overlay ---------------------------------------------------------------------
  drawOverlay(
    hover: Tile | null,
    preview: { rect: Rect; ok: boolean; entrance?: Tile } | null,
    tool: "path" | "erase" | "gate" | "inspect" | "place",
  ) {
    const o = this.overlay.clear();
    if (preview) {
      const { rect, ok, entrance } = preview;
      o.rect(rect.x * TILE, rect.y * TILE, rect.w * TILE, rect.h * TILE)
        .fill({ color: ok ? 0x6ee7a0 : 0xff5d5d, alpha: 0.45 })
        .stroke({ width: 2, color: ok ? 0x0e7a3e : 0xb3261e });
      if (entrance)
        o.circle((entrance[0] + 0.5) * TILE, (entrance[1] + 0.5) * TILE, 5).fill(0x0e7a3e);
      return;
    }
    if (!hover || tool === "inspect") return;
    const color = tool === "erase" ? 0xff5d5d : tool === "gate" ? 0xf5c542 : 0xffffff;
    o.rect(hover[0] * TILE, hover[1] * TILE, TILE, TILE)
      .fill({ color, alpha: 0.35 })
      .stroke({ width: 2, color, alpha: 0.9 });
  }

  // ---- live simulation -------------------------------------------------------------------
  showStatuses(
    frames: AttractionFrame[],
    layout: EditableLayout,
    catalog: Map<string, Template>,
    clockSeconds: number,
  ) {
    const seen = new Set<string>();
    for (const f of frames) {
      const spec = layout.building(f.id);
      const template = spec && catalog.get(spec.template);
      if (!spec || !template) continue;
      const body = this.bodies.get(f.id);
      if (body) body.alpha = f.status === "open" ? 1 : 0.55;

      let text = "";
      let color = 0x2e9d5b;
      if (f.status !== "open") {
        text = STATUS_LABEL[f.status] ?? f.status;
        color = f.status === "weather" ? 0x5b6b8c : 0x4b4b4b;
      } else if (template.category === "ride") {
        const wait = f.posted ?? 5;
        text = `${wait} min`;
        color = wait <= 20 ? 0x2e9d5b : wait <= 45 ? 0xd9911a : 0xd64541;
      } else if (template.category === "show") {
        if (f.next_show != null && f.next_show - clockSeconds <= 3600) {
          text = `🎭 ${fmtClock(f.next_show)}`;
          color = 0x7046a8;
        }
      } else if ((f.wait ?? 0) >= 3) {
        text = `${f.wait} min`;
        color = 0x2f6fb0;
      }
      if (!text) continue;
      seen.add(f.id);
      const r = layout.rectOf(spec, catalog);
      this.setBadge(f.id, text, color, (r.x + r.w / 2) * TILE, r.y * TILE);
    }
    for (const [id, badge] of this.badges) {
      if (!seen.has(id)) {
        badge.box.destroy();
        badge.label.destroy();
        this.badges.delete(id);
      }
    }
  }

  private setBadge(id: string, text: string, color: number, cx: number, top: number) {
    const key = `${text}|${color}`;
    let badge = this.badges.get(id);
    if (!badge) {
      const box = new Graphics();
      const label = new Text({
        text,
        style: { fontFamily: "Inter, system-ui, sans-serif", fontSize: 10, fontWeight: "700", fill: 0xffffff },
        resolution: 3,
      });
      label.anchor.set(0.5);
      this.badgeLayer.addChild(box, label);
      badge = { box, label, key: "" };
      this.badges.set(id, badge);
    }
    if (badge.key !== key) {
      badge.label.text = text;
      const w = badge.label.width + 10;
      badge.box.clear().roundRect(-w / 2, -8, w, 16, 8).fill(color).stroke({ width: 1, color: 0xffffff, alpha: 0.8 });
      badge.key = key;
    }
    badge.box.position.set(cx, top);
    badge.label.position.set(cx, top);
  }

  clearLive() {
    for (const badge of this.badges.values()) {
      badge.box.destroy();
      badge.label.destroy();
    }
    this.badges.clear();
    for (const body of this.bodies.values()) body.alpha = 1;
    for (const d of this.dots.values()) this.release(d.sprite);
    this.dots.clear();
  }

  /** New guest positions; dots glide from where they are to the new spot over one frame. */
  setGuests(flat: number[], interval: number) {
    const now = performance.now();
    this.frameInterval = Math.max(interval, 50);
    this.frameAt = now;
    const present = new Set<number>();
    for (let i = 0; i < flat.length; i += 4) {
      const id = flat[i];
      const x = (flat[i + 1] / 10) * TILE;
      const y = (flat[i + 2] / 10) * TILE;
      present.add(id);
      let d = this.dots.get(id);
      if (!d) {
        const sprite = this.pool.pop() ?? new Sprite(this.dot);
        sprite.anchor.set(0.5);
        sprite.scale.set(0.55);
        sprite.position.set(x, y);
        sprite.visible = true;
        this.guestLayer.addChild(sprite);
        d = { sprite, fx: x, fy: y, tx: x, ty: y };
        this.dots.set(id, d);
      } else {
        d.fx = d.sprite.x;
        d.fy = d.sprite.y;
        d.tx = x;
        d.ty = y;
      }
      d.sprite.tint = MOOD_COLORS[flat[i + 3]] ?? 0xffffff;
    }
    for (const [id, d] of this.dots) {
      if (!present.has(id)) {
        this.release(d.sprite);
        this.dots.delete(id);
      }
    }
  }

  private release(sprite: Sprite) {
    sprite.visible = false;
    this.guestLayer.removeChild(sprite);
    this.pool.push(sprite);
  }

  private animateGuests() {
    if (!this.dots.size) return;
    const k = Math.min((performance.now() - this.frameAt) / this.frameInterval, 1);
    for (const d of this.dots.values())
      d.sprite.position.set(d.fx + (d.tx - d.fx) * k, d.fy + (d.ty - d.fy) * k);
  }
}

export function fmtClock(seconds: number) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}
