// WS-LOCAL Step #1 — Seed-only PBIP-to-disk writer.
//
// Spec lives in `components/PbiFixer/PLAN.md` ("WS-LOCAL — Local ↔
// online round-trip"). This module owns the browser-side filesystem
// glue: validate the File System Access API, ask the user to pick a
// folder (must be called from a user-activation handler), and write
// the live `getDefinition` parts 1:1 into nested subdirectories under
// `<displayName>.SemanticModel/` and `<displayName>.Report/`, plus a
// minimal `<displayName>.pbip` shortcut at the root.
//
// Out of scope (later WS-LOCAL ship steps): IndexedDB handle reuse,
// diff dialog, mismatch confirm, PBIX export, push direction.

export interface SeedPart {
  /** Part path relative to the artifact root, e.g. `definition.pbism`
   *  or `definition/tables/Sales.tmdl`. */
  path: string;
  /** Base64-encoded payload as returned by Fabric `getDefinition`. */
  payload: string;
  /** Kept for symmetry with the Fabric REST shape; unused here. */
  payloadType?: string;
}

export interface SeedInputs {
  /** Folder picked via `showDirectoryPicker`. */
  dirHandle: FileSystemDirectoryHandle;
  /** Sanitised display name of the connected dataset / report. Used
   *  for both subfolder names and the `.pbip` shortcut filename. */
  displayName: string;
  /** TMDL parts for the semantic model side, if any. */
  modelParts?: SeedPart[];
  /** PBIR / PBIR-Legacy parts for the report side, if any. */
  reportParts?: SeedPart[];
}

export interface SeedResult {
  written: number;
  errors: { path: string; message: string }[];
}

/** True when the current document can call `showDirectoryPicker`.
 *  Chromium-only at the time of writing — Firefox / Safari return false
 *  and the caller should surface a "not supported" hint. */
export function isFsaSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof (window as unknown as { showDirectoryPicker?: unknown })
      .showDirectoryPicker === "function"
  );
}

/** Open the native folder picker. MUST be called synchronously from a
 *  user-gesture handler (click / keydown), otherwise the browser
 *  rejects the call with `SecurityError`. */
export async function pickPbipFolder(): Promise<FileSystemDirectoryHandle> {
  if (!isFsaSupported()) {
    throw new Error(
      "File System Access API not available in this browser. Use Edge or Chrome.",
    );
  }
  // Cast — TS lib may not include the FSA types depending on tsconfig.
  const showDirectoryPicker = (
    window as unknown as {
      showDirectoryPicker: (opts: {
        mode: "readwrite";
        id?: string;
      }) => Promise<FileSystemDirectoryHandle>;
    }
  ).showDirectoryPicker;
  return showDirectoryPicker({ mode: "readwrite", id: "pbi-fixer-local" });
}

/** Sanitise a Fabric display name so it's safe to use as a folder name
 *  on Windows (no `<>:"/\|?*`, no trailing dots / spaces). Empty string
 *  if every char is stripped. */
export function sanitisePbipName(raw: string): string {
  return raw
    .replace(/[<>:"/\\|?*\u0000-\u001F]+/g, "_")
    .replace(/[. ]+$/g, "")
    .trim();
}

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

/** Walk `path` (forward-slash separated) under `root`, creating each
 *  intermediate directory as needed, and return the final file handle
 *  ready for `createWritable`. */
async function ensureFileHandle(
  root: FileSystemDirectoryHandle,
  path: string,
): Promise<FileSystemFileHandle> {
  const segments = path.split(/[\\/]+/).filter(Boolean);
  if (segments.length === 0) {
    throw new Error("Empty part path");
  }
  let dir = root;
  for (let i = 0; i < segments.length - 1; i++) {
    dir = await dir.getDirectoryHandle(segments[i], { create: true });
  }
  return dir.getFileHandle(segments[segments.length - 1], { create: true });
}

async function writeBinary(
  root: FileSystemDirectoryHandle,
  path: string,
  bytes: Uint8Array,
): Promise<void> {
  const fileHandle = await ensureFileHandle(root, path);
  const writable = await (fileHandle as unknown as {
    createWritable: () => Promise<FileSystemWritableFileStream>;
  }).createWritable();
  try {
    await writable.write(bytes);
  } finally {
    await writable.close();
  }
}

async function writeText(
  root: FileSystemDirectoryHandle,
  path: string,
  text: string,
): Promise<void> {
  const bytes = new TextEncoder().encode(text);
  await writeBinary(root, path, bytes);
}

/** Build the minimal `.pbip` shortcut JSON. PBI Desktop accepts a
 *  pretty short payload — `version` + `artifacts[].report.path` is
 *  enough for double-click to open. We point at the report subfolder
 *  if a report is present, otherwise at the model subfolder. */
function buildPbipShortcut(
  displayName: string,
  hasReport: boolean,
): string {
  const targetPath = hasReport
    ? `${displayName}.Report`
    : `${displayName}.SemanticModel`;
  return JSON.stringify(
    {
      version: "1.0",
      artifacts: [{ report: { path: targetPath } }],
      settings: { enableAutoRecovery: true },
    },
    null,
    2,
  );
}

/** Write every part of the live model + report into a fresh PBIP
 *  layout under `dirHandle`. Returns the count of files written and
 *  any per-file errors (so a single bad write does not abort the
 *  whole seed). */
export async function seedPbipToDisk(opts: SeedInputs): Promise<SeedResult> {
  const safeName = sanitisePbipName(opts.displayName);
  if (!safeName) {
    throw new Error("Display name is empty after sanitisation");
  }

  const result: SeedResult = { written: 0, errors: [] };

  const writePart = async (
    artifactRoot: FileSystemDirectoryHandle,
    part: SeedPart,
  ) => {
    try {
      const bytes = base64ToBytes(part.payload);
      await writeBinary(artifactRoot, part.path, bytes);
      result.written += 1;
    } catch (e) {
      result.errors.push({
        path: part.path,
        message: e instanceof Error ? e.message : String(e),
      });
    }
  };

  if (opts.modelParts && opts.modelParts.length > 0) {
    const modelRoot = await opts.dirHandle.getDirectoryHandle(
      `${safeName}.SemanticModel`,
      { create: true },
    );
    for (const part of opts.modelParts) {
      await writePart(modelRoot, part);
    }
  }

  if (opts.reportParts && opts.reportParts.length > 0) {
    const reportRoot = await opts.dirHandle.getDirectoryHandle(
      `${safeName}.Report`,
      { create: true },
    );
    for (const part of opts.reportParts) {
      await writePart(reportRoot, part);
    }
  }

  // .pbip shortcut at the root — only meaningful if at least one
  // artifact landed.
  if (result.written > 0) {
    try {
      const hasReport = !!(opts.reportParts && opts.reportParts.length > 0);
      await writeText(
        opts.dirHandle,
        `${safeName}.pbip`,
        buildPbipShortcut(safeName, hasReport),
      );
      result.written += 1;
    } catch (e) {
      result.errors.push({
        path: `${safeName}.pbip`,
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }

  return result;
}
