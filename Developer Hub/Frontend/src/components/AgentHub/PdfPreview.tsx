import React, { useEffect, useRef, useState } from "react";
import { Spinner } from "@fluentui/react-components";

/**
 * Inline PDF viewer backed by PDF.js.
 *
 * Why not just `<iframe src=blob:…>` or `<object>`? Fabric hosts this
 * workload inside a sandboxed cross-origin iframe; browsers (Edge in
 * particular) then block both `data:application/pdf` and blob-URL PDFs
 * from rendering inline, and also block `<a target="_blank">` / download
 * attributes from escaping the sandbox. PDF.js renders each page to a
 * `<canvas>` inside the current origin — no iframe, no popup, no
 * download — so every one of those policies becomes irrelevant.
 *
 * Accepts either a `data:application/pdf;base64,…` URI or a raw
 * `Uint8Array`. Renders every page vertically; container scrolls.
 */
export function PdfPreview({ source, filename }: { source: string | Uint8Array; filename?: string }) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        async function run() {
            setLoading(true);
            setError(null);
            const container = containerRef.current;
            if (!container) return;
            container.innerHTML = "";
            try {
                // Dynamic import keeps pdfjs out of the main bundle until a
                // PDF is actually opened.
                // @ts-ignore — no bundled type declarations for the ESM entry
                const pdfjs: any = await import("pdfjs-dist/build/pdf.mjs");
                // The worker is copied to the dist root by CopyWebpackPlugin
                // (see tools/webpack.config.js) and served at the static
                // path below. We deliberately avoid `new URL(..., import.meta.url)`
                // because our tsconfig target (ES2017) predates `import.meta`
                // and the transpiled output trips the URL constructor at
                // runtime ("Failed to construct 'URL': Invalid URL").
                if (!pdfjs.GlobalWorkerOptions.workerSrc
                    && !pdfjs.GlobalWorkerOptions.workerPort) {
                    pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.js";
                }

                // Normalize input to Uint8Array.
                let data: Uint8Array;
                if (typeof source === "string") {
                    if (source.startsWith("data:")) {
                        const comma = source.indexOf(",");
                        const header = source.slice(5, comma);
                        const payload = source.slice(comma + 1);
                        if (header.includes(";base64")) {
                            const bin = atob(payload);
                            data = new Uint8Array(bin.length);
                            for (let i = 0; i < bin.length; i++) data[i] = bin.charCodeAt(i);
                        } else {
                            data = new TextEncoder().encode(decodeURIComponent(payload));
                        }
                    } else {
                        // Treat as URL (blob: or http:). Fetch bytes.
                        const resp = await fetch(source);
                        data = new Uint8Array(await resp.arrayBuffer());
                    }
                } else {
                    data = source;
                }

                const loadingTask = pdfjs.getDocument({ data });
                const pdf = await loadingTask.promise;
                if (cancelled) { pdf.destroy(); return; }
                const dpr = Math.min(window.devicePixelRatio || 1, 2);
                const containerWidth = container.clientWidth - 16;
                for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
                    if (cancelled) break;
                    const page = await pdf.getPage(pageNum);
                    const baseViewport = page.getViewport({ scale: 1 });
                    // Fit to container width.
                    const scale = Math.max(0.5, containerWidth / baseViewport.width);
                    const viewport = page.getViewport({ scale });
                    const canvas = document.createElement("canvas");
                    canvas.className = "pdf-preview-page";
                    canvas.width = Math.ceil(viewport.width * dpr);
                    canvas.height = Math.ceil(viewport.height * dpr);
                    canvas.style.width = `${Math.floor(viewport.width)}px`;
                    canvas.style.height = `${Math.floor(viewport.height)}px`;
                    const ctx = canvas.getContext("2d");
                    if (!ctx) continue;
                    ctx.scale(dpr, dpr);
                    container.appendChild(canvas);
                    await page.render({ canvasContext: ctx, viewport }).promise;
                }
                if (!cancelled) setLoading(false);
            } catch (e: any) {
                console.error("PDF render failed", e);
                if (!cancelled) {
                    setError(e?.message || "Failed to render PDF");
                    setLoading(false);
                }
            }
        }
        run();
        return () => { cancelled = true; };
    }, [source]);

    return (
        <div className="pdf-preview-wrap">
            {loading && (
                <div className="pdf-preview-status">
                    <Spinner size="small" />
                    <span>Rendering {filename || "PDF"}…</span>
                </div>
            )}
            {error && (
                <div className="pdf-preview-status pdf-preview-status--error">
                    Couldn't render the PDF: {error}
                </div>
            )}
            <div ref={containerRef} className="pdf-preview-pages" />
        </div>
    );
}
