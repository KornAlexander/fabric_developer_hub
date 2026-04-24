// WS-M — Prototype API & PBIR skeleton export.
//
// First cut: build a client-side PBIR-lite skeleton describing pages,
// visuals and field bindings. Downloaded as JSON from the browser —
// good enough to feed into a conversion pipeline or document a design.
//
// Uploading the skeleton to a workspace (as a real PBIR report) is
// deferred until the backend `semantic-link-labs` bridge lands.

import type { PbiAuth } from "./fabricApi";
import type { ReportData } from "../types/report";

export type VisualType =
  | "card"
  | "table"
  | "matrix"
  | "barChart"
  | "columnChart"
  | "lineChart"
  | "pieChart"
  | "slicer";

export interface FieldRef {
  /** Role hint — "Values" for measures, "Category" / "Legend" / "Axis"
   *  for columns. Free-form, the exporter respects whatever you set. */
  role: string;
  tableName: string;
  /** Column or measure name. */
  propertyName: string;
  kind: "column" | "measure";
}

export interface PrototypeVisual {
  id: string;
  type: VisualType;
  title: string;
  x: number;
  y: number;
  width: number;
  height: number;
  fields: FieldRef[];
}

export interface PrototypePage {
  id: string;
  name: string;
  width: number;
  height: number;
  visuals: PrototypeVisual[];
}

export interface PrototypeDocument {
  version: "pbir-skeleton/1.0";
  reportName: string;
  datasetName?: string;
  datasetId?: string;
  workspaceId?: string;
  pages: PrototypePage[];
}

/** Convert an in-memory Prototype document to a PBIR-lite JSON string. */
export function exportPrototypeToPbir(doc: PrototypeDocument): string {
  return JSON.stringify(doc, null, 2);
}

/** Trigger a browser download of the exported JSON. */
export function downloadJson(filename: string, content: string): void {
  const blob = new Blob([content], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Generic single-blob downloader (text content). */
export function downloadText(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ------------------------------------------------------------------ */
/* Reverse prototype — load existing PBI report → PrototypeDocument   */
/* ------------------------------------------------------------------ */
//
// Mirrors the Python `_report_prototype.generate_report_prototype`
// flow: walk the parsed PBIR `ReportData` (pages + visuals with
// position/title/type) and build a fully editable PrototypeDocument.
// The Power BI native visual type taxonomy is much larger than the
// Prototype canvas's eight-way enum, so unknown types fold to "card"
// (with the original PBI type preserved in the title for clarity).
//
// Field bindings are NOT extracted — the per-visual `query.json`
// parts contain the field bindings but mapping them back to a
// table[column] reference reliably requires resolving DAX projections
// against the model. Out of scope for v1; users can re-bind in the
// canvas after import.

/** Map a Power BI native visual type to the PrototypeDocument enum. */
export function mapPbiVisualType(pbiType: string): VisualType {
  const t = (pbiType || "").toLowerCase();
  if (t.includes("multirowcard") || t === "card" || t === "cardvisual") return "card";
  if (t.includes("matrix") || t.includes("pivot")) return "matrix";
  if (t === "tableex" || t === "table" || t.includes("tablevisual")) return "table";
  if (t.includes("bar")) return "barChart";
  if (t.includes("column")) return "columnChart";
  if (t.includes("line") || t.includes("area")) return "lineChart";
  if (t.includes("pie") || t.includes("donut")) return "pieChart";
  if (t.includes("slicer")) return "slicer";
  return "card";
}

/** Build a PrototypeDocument from a parsed PBI ReportData (PBIR). */
export function reportToPrototypeDocument(
  report: ReportData,
  reportName: string,
  opts: { includeHidden?: boolean } = {},
): PrototypeDocument {
  const includeHidden = opts.includeHidden ?? false;

  // Sort pages by ordinal (the order set in pages.json).
  const pageEntries = Object.entries(report.pages)
    .filter(([, pg]) => includeHidden || !pg.hidden)
    .sort(([, a], [, b]) => (a.ordinal ?? 9999) - (b.ordinal ?? 9999));

  const pages: PrototypePage[] = pageEntries.map(([pageId, pg]) => {
    const visuals: PrototypeVisual[] = Object.entries(pg.visuals)
      .filter(([, v]) => includeHidden || !v.hidden)
      .map(([visualId, v]) => {
        const mapped = mapPbiVisualType(v.type);
        const title =
          (v.title && v.title.trim()) ||
          (v.type ? `[${v.type}]` : visualId);
        return {
          id: visualId,
          type: mapped,
          title,
          x: v.x ?? 0,
          y: v.y ?? 0,
          width: v.width ?? 200,
          height: v.height ?? 150,
          fields: [],
        } as PrototypeVisual;
      });

    return {
      id: pageId,
      name: pg.displayName || pageId,
      width: pg.width || 1280,
      height: pg.height || 720,
      visuals,
    };
  });

  return {
    version: "pbir-skeleton/1.0",
    reportName: reportName || "Reverse-prototype",
    workspaceId: report.workspaceId,
    pages,
  };
}

/* ------------------------------------------------------------------ */
/* Excalidraw export                                                  */
/* ------------------------------------------------------------------ */
//
// Native Excalidraw scene format — drop this .excalidraw file onto
// excalidraw.com (or use File ▸ Open) and it imports as editable
// shapes. Each Prototype page becomes a labelled rectangle frame
// (with a header text), each visual becomes a rectangle + title
// text + small type tag. Pages are laid out vertically with a 60 px
// gap so they don't overlap.
//
// File-format reference: https://docs.excalidraw.com/docs/codebase/json-schema
//
// We only emit the subset of fields Excalidraw requires; everything
// else falls back to its defaults on import.

interface ExcalidrawElementBase {
  id: string;
  type: "rectangle" | "text";
  x: number;
  y: number;
  width: number;
  height: number;
  angle: number;
  strokeColor: string;
  backgroundColor: string;
  fillStyle: "solid" | "hachure" | "cross-hatch";
  strokeWidth: number;
  strokeStyle: "solid" | "dashed" | "dotted";
  roughness: number;
  opacity: number;
  groupIds: string[];
  frameId: null | string;
  roundness: { type: number } | null;
  seed: number;
  versionNonce: number;
  isDeleted: false;
  boundElements: null;
  updated: number;
  link: null;
  locked: false;
  index?: string;
}

interface ExcalidrawText extends ExcalidrawElementBase {
  type: "text";
  text: string;
  fontSize: number;
  fontFamily: number; // 1=Virgil, 2=Helvetica, 3=Cascadia
  textAlign: "left" | "center" | "right";
  verticalAlign: "top" | "middle" | "bottom";
  baseline: number;
  containerId: null;
  originalText: string;
  lineHeight: number;
  autoResize: boolean;
}

interface ExcalidrawRect extends ExcalidrawElementBase {
  type: "rectangle";
}

type ExcalidrawElement = ExcalidrawRect | ExcalidrawText;

interface ExcalidrawScene {
  type: "excalidraw";
  version: 2;
  source: string;
  elements: ExcalidrawElement[];
  appState: {
    gridSize: number | null;
    viewBackgroundColor: string;
  };
  files: Record<string, never>;
}

let excalidrawSeed = 1;
function nextSeed(): number {
  excalidrawSeed = (excalidrawSeed * 9301 + 49297) % 233280;
  return excalidrawSeed;
}

function makeRect(
  id: string,
  x: number, y: number, w: number, h: number,
  fill: string, stroke: string,
): ExcalidrawRect {
  return {
    id, type: "rectangle",
    x, y, width: w, height: h, angle: 0,
    strokeColor: stroke, backgroundColor: fill,
    fillStyle: "solid", strokeWidth: 1, strokeStyle: "solid",
    roughness: 0, opacity: 100,
    groupIds: [], frameId: null,
    roundness: { type: 3 },
    seed: nextSeed(), versionNonce: nextSeed(),
    isDeleted: false, boundElements: null, updated: Date.now(),
    link: null, locked: false,
  };
}

function makeText(
  id: string,
  x: number, y: number, w: number, h: number,
  text: string, fontSize: number, color: string,
  align: "left" | "center" = "left",
): ExcalidrawText {
  return {
    id, type: "text",
    x, y, width: w, height: h, angle: 0,
    strokeColor: color, backgroundColor: "transparent",
    fillStyle: "solid", strokeWidth: 1, strokeStyle: "solid",
    roughness: 0, opacity: 100,
    groupIds: [], frameId: null, roundness: null,
    seed: nextSeed(), versionNonce: nextSeed(),
    isDeleted: false, boundElements: null, updated: Date.now(),
    link: null, locked: false,
    text, fontSize, fontFamily: 3, // Cascadia (mono-ish, neutral)
    textAlign: align, verticalAlign: "top",
    baseline: Math.round(fontSize * 0.85),
    containerId: null, originalText: text,
    lineHeight: 1.25, autoResize: true,
  };
}

const VISUAL_FILL: Record<VisualType, string> = {
  card:        "#dbeafe",
  table:       "#fef3c7",
  matrix:      "#fde68a",
  barChart:    "#bbf7d0",
  columnChart: "#a7f3d0",
  lineChart:   "#bae6fd",
  pieChart:    "#fbcfe8",
  slicer:      "#e9d5ff",
};

/** Build an Excalidraw scene from the Prototype document. */
export function exportPrototypeToExcalidraw(doc: PrototypeDocument): string {
  excalidrawSeed = 1;
  const elements: ExcalidrawElement[] = [];
  const PAGE_GAP = 60;
  const HEADER_H = 28;
  let cursorY = 0;
  let counter = 0;
  const newId = () => `el-${++counter}`;

  for (const page of doc.pages) {
    const pageX = 0;
    const pageY = cursorY;
    // Page header
    elements.push(makeText(
      newId(), pageX, pageY, page.width, HEADER_H,
      page.name, 20, "#1f2937",
    ));
    // Page frame (a soft outlined rect)
    elements.push(makeRect(
      newId(), pageX, pageY + HEADER_H, page.width, page.height,
      "#ffffff", "#94a3b8",
    ));
    // Visuals
    for (const v of page.visuals) {
      const vx = pageX + v.x;
      const vy = pageY + HEADER_H + v.y;
      const fill = VISUAL_FILL[v.type] || "#e5e7eb";
      elements.push(makeRect(newId(), vx, vy, v.width, v.height, fill, "#475569"));
      elements.push(makeText(
        newId(), vx + 8, vy + 6, v.width - 16, 22,
        v.title || v.type, 14, "#0f172a",
      ));
      elements.push(makeText(
        newId(), vx + 8, vy + 28, v.width - 16, 16,
        v.type, 11, "#475569",
      ));
      if (v.fields.length) {
        const fieldsLine = v.fields
          .slice(0, 5)
          .map(f => `${f.role}: ${f.tableName}[${f.propertyName}]`)
          .join("  ·  ") + (v.fields.length > 5 ? `  …(+${v.fields.length - 5})` : "");
        elements.push(makeText(
          newId(), vx + 8, vy + 46, v.width - 16, 16,
          fieldsLine, 10, "#334155",
        ));
      }
    }
    cursorY += HEADER_H + page.height + PAGE_GAP;
  }

  const scene: ExcalidrawScene = {
    type: "excalidraw",
    version: 2,
    source: "https://github.com/LukaszObst/fabric_developer_hub",
    elements,
    appState: { gridSize: null, viewBackgroundColor: "#ffffff" },
    files: {},
  };
  return JSON.stringify(scene, null, 2);
}

/* ------------------------------------------------------------------ */
/* Figma export (SVG)                                                 */
/* ------------------------------------------------------------------ */
//
// Figma has no public open scene format. The cleanest interop path is
// SVG: drag-drop the .svg onto a Figma canvas and Figma imports each
// `<g>` as a frame and each `<rect>` / `<text>` as an editable layer.
// We emit one combined SVG with all pages stacked vertically; each
// page is wrapped in a `<g>` named after the page so it lands as a
// titled frame in Figma.

function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/** Build an SVG (Figma-importable) from the Prototype document. */
export function exportPrototypeToSvg(doc: PrototypeDocument): string {
  const PAGE_GAP = 60;
  const HEADER_H = 28;
  const totalW = Math.max(800, ...doc.pages.map(p => p.width));
  const totalH = doc.pages.reduce(
    (acc, p) => acc + HEADER_H + p.height + PAGE_GAP,
    PAGE_GAP,
  );

  const out: string[] = [];
  out.push(`<?xml version="1.0" encoding="UTF-8"?>`);
  out.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${totalW}" height="${totalH}" viewBox="0 0 ${totalW} ${totalH}" font-family="Segoe UI, Helvetica, Arial, sans-serif">`,
  );
  out.push(`<title>${escapeXml(doc.reportName)}</title>`);
  out.push(`<rect x="0" y="0" width="${totalW}" height="${totalH}" fill="#ffffff"/>`);

  let cursorY = PAGE_GAP / 2;
  for (const page of doc.pages) {
    out.push(`<g id="${escapeXml(page.id)}" data-name="${escapeXml(page.name)}">`);
    // Header text
    out.push(
      `<text x="0" y="${cursorY + 20}" font-size="20" font-weight="600" fill="#1f2937">${escapeXml(page.name)}</text>`,
    );
    const frameY = cursorY + HEADER_H;
    out.push(
      `<rect x="0" y="${frameY}" width="${page.width}" height="${page.height}" fill="#ffffff" stroke="#94a3b8" stroke-width="1" rx="6"/>`,
    );
    for (const v of page.visuals) {
      const vx = v.x;
      const vy = frameY + v.y;
      const fill = VISUAL_FILL[v.type] || "#e5e7eb";
      out.push(`<g id="${escapeXml(v.id)}" data-name="${escapeXml(v.title || v.type)}">`);
      out.push(
        `<rect x="${vx}" y="${vy}" width="${v.width}" height="${v.height}" fill="${fill}" stroke="#475569" stroke-width="1" rx="4"/>`,
      );
      out.push(
        `<text x="${vx + 8}" y="${vy + 22}" font-size="14" font-weight="600" fill="#0f172a">${escapeXml(v.title || v.type)}</text>`,
      );
      out.push(
        `<text x="${vx + 8}" y="${vy + 40}" font-size="11" fill="#475569">${escapeXml(v.type)}</text>`,
      );
      if (v.fields.length) {
        const fieldsLine = v.fields
          .slice(0, 5)
          .map(f => `${f.role}: ${f.tableName}[${f.propertyName}]`)
          .join("  ·  ") + (v.fields.length > 5 ? `  …(+${v.fields.length - 5})` : "");
        out.push(
          `<text x="${vx + 8}" y="${vy + 58}" font-size="10" fill="#334155">${escapeXml(fieldsLine)}</text>`,
        );
      }
      out.push(`</g>`);
    }
    out.push(`</g>`);
    cursorY += HEADER_H + page.height + PAGE_GAP;
  }
  out.push(`</svg>`);
  return out.join("\n");
}

/** Stub — uploading the PBIR as a new report in the workspace lands
 *  when the backend bridge is wired. */
export async function uploadPrototypeAsReport(
  _auth: PbiAuth,
  _workspaceId: string,
  _doc: PrototypeDocument,
): Promise<{ uploaded: boolean; message: string }> {
  return {
    uploaded: false,
    message:
      "Backend bridge (PBIR upload via sempy-labs / createReport) not yet wired — use Export JSON.",
  };
}
