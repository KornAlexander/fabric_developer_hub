import "@testing-library/jest-dom/vitest";
import { beforeAll, afterAll, afterEach, vi } from "vitest";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

// Silence i18next "not initialized" warnings — we inject a minimal
// translation store so that every key used in the Plan view resolves
// to something that contains the key (so tests can assert on fragments).
beforeAll(async () => {
    if (!i18n.isInitialized) {
        await i18n.use(initReactI18next).init({
            lng: "en-US",
            fallbackLng: "en-US",
            resources: {
                "en-US": {
                    translation: new Proxy(
                        {},
                        {
                            // Any unknown key → return its own name so we never
                            // see "missing_key" placeholders in snapshots.
                            get: (_t, key: string) => key.replace(/_/g, " "),
                        }
                    ) as Record<string, string>,
                },
            },
            interpolation: { escapeValue: false },
        });
    }
});

// Polyfill ResizeObserver used by Fluent UI primitives.
if (typeof globalThis.ResizeObserver === "undefined") {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).ResizeObserver = class {
        observe(): void {
            /* noop */
        }
        unobserve(): void {
            /* noop */
        }
        disconnect(): void {
            /* noop */
        }
    };
}

afterEach(() => {
    vi.clearAllMocks();
});

afterAll(() => {
    /* cleanup hook reserved for MSW server.close() */
});
