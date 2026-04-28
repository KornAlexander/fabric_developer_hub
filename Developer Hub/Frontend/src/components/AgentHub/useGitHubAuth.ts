/**
 * Shared GitHub Device Flow authentication hook.
 * Used by both the ChatPanel and the AgentHub.
 */
import { useState, useRef, useEffect, useCallback } from 'react';

declare const process: { env: Record<string, string | undefined> };
const BE = process.env.WORKLOAD_BE_URL || 'http://127.0.0.1:5000';

/**
 * Token storage helper.
 *
 * We prefer ``localStorage`` so the GitHub token survives iframe reloads
 * and new Fabric portal sessions — otherwise the user is prompted to
 * sign in every time the workload iframe is recreated (which happens on
 * every Fabric portal reload because iframes start with empty
 * ``sessionStorage``).
 *
 * Some sandboxed iframes block ``localStorage``. If the access throws
 * (SecurityError / QuotaExceededError), we transparently fall back to
 * ``sessionStorage`` so behaviour degrades rather than crashing.
 */
function makeStorage() {
    const probe = (s: Storage | undefined): Storage | null => {
        if (!s) return null;
        try {
            const k = '__agenthub_probe__';
            s.setItem(k, '1');
            s.removeItem(k);
            return s;
        } catch {
            return null;
        }
    };
    const local = probe(typeof window !== 'undefined' ? window.localStorage : undefined);
    const session = probe(typeof window !== 'undefined' ? window.sessionStorage : undefined);
    return {
        // Reads prefer localStorage (survives iframe reload) and fall
        // back to sessionStorage. On a hit in localStorage we *mirror*
        // into sessionStorage so legacy call sites elsewhere in the app
        // that read ``sessionStorage.getItem('github_token')`` directly
        // keep working in the current tab without needing an edit.
        get(key: string): string | null {
            try {
                const v = local?.getItem(key);
                if (v !== null && v !== undefined) {
                    try { session?.setItem(key, v); } catch { /* ignore */ }
                    return v;
                }
            } catch { /* ignore */ }
            try { return session?.getItem(key) ?? null; } catch { return null; }
        },
        // Writes go to *both* stores: localStorage for persistence,
        // sessionStorage to keep existing direct readers happy.
        set(key: string, value: string): void {
            try { local?.setItem(key, value); } catch { /* ignore */ }
            try { session?.setItem(key, value); } catch { /* ignore */ }
        },
        remove(key: string): void {
            try { local?.removeItem(key); } catch { /* ignore */ }
            try { session?.removeItem(key); } catch { /* ignore */ }
        },
    };
}
const tokenStore = makeStorage();
const GH_TOKEN_KEY = 'github_token';
const GH_USER_KEY = 'github_user';

export interface DeviceFlow {
    userCode: string;
    verificationUri: string;
    /** GitHub's URL with the user code pre-embedded — opening this
     *  skips the "paste your code" step and takes the user straight
     *  to the Authorize screen. May be absent for older GitHub apps. */
    verificationUriComplete?: string;
    deviceCode: string;
    interval: number;
    expiresIn: number;
}

export interface GitHubAuth {
    githubToken: string | null;
    githubUser: string | null;
    isPolling: boolean;
    deviceFlow: DeviceFlow | null;
    codeCopied: boolean;
    /** Starts the device flow and resolves to the flow descriptor (or
     *  null on error). Callers can use the returned
     *  ``verificationUriComplete`` to open GitHub's Authorize page in
     *  the same user gesture that invoked sign-in. */
    startDeviceFlow: () => Promise<DeviceFlow | null>;
    copyCode: () => void;
    signOut: () => void;
}

export function useGitHubAuth(): GitHubAuth {
    const [githubToken, setGithubToken] = useState<string | null>(
        () => tokenStore.get(GH_TOKEN_KEY)
    );
    const [githubUser, setGithubUser] = useState<string | null>(
        () => tokenStore.get(GH_USER_KEY)
    );
    const [deviceFlow, setDeviceFlow] = useState<any>(null);
    const [isPolling, setIsPolling] = useState(false);
    const [codeCopied, setCodeCopied] = useState(false);
    const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

    useEffect(() => {
        return () => {
            if (pollingRef.current) clearInterval(pollingRef.current);
        };
    }, []);

    const startDeviceFlow = useCallback(async (): Promise<DeviceFlow | null> => {
        try {
            const resp = await fetch(`${BE}/api/github/device-code`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            if (!resp.ok) throw new Error('Device flow start failed');
            const data = await resp.json();
            const flow: DeviceFlow = {
                userCode: data.user_code,
                verificationUri: data.verification_uri,
                verificationUriComplete: data.verification_uri_complete,
                deviceCode: data.device_code,
                interval: data.interval,
                expiresIn: data.expires_in,
            };
            setDeviceFlow(flow);
            setCodeCopied(false);

            // Auto-copy code to clipboard
            try {
                await navigator.clipboard.writeText(flow.userCode);
                setCodeCopied(true);
            } catch { /* clipboard may not be available in sandbox */ }

            // Poll for token
            setIsPolling(true);
            pollingRef.current = setInterval(async () => {
                try {
                    const pollResp = await fetch(`${BE}/api/github/poll-token`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ device_code: flow.deviceCode }),
                    });
                    const pollData = await pollResp.json();
                    if (pollData.status === 'complete' && pollData.access_token) {
                        if (pollingRef.current) clearInterval(pollingRef.current);
                        setIsPolling(false);
                        setDeviceFlow(null);
                        tokenStore.set(GH_TOKEN_KEY, pollData.access_token);
                        if (pollData.github_user) tokenStore.set(GH_USER_KEY, pollData.github_user);
                        setGithubToken(pollData.access_token);
                        setGithubUser(pollData.github_user || null);
                    } else if (pollData.status === 'expired' || pollData.status === 'error') {
                        if (pollingRef.current) clearInterval(pollingRef.current);
                        setIsPolling(false);
                        setDeviceFlow(null);
                    }
                } catch {
                    // keep polling
                }
            }, (flow.interval + 1) * 1000);
            return flow;
        } catch (e) {
            console.error('Device flow error:', e);
            return null;
        }
    }, []);

    const copyCode = useCallback(() => {
        if (deviceFlow?.userCode) {
            navigator.clipboard.writeText(deviceFlow.userCode).then(() => setCodeCopied(true));
        }
    }, [deviceFlow]);

    const signOut = useCallback(() => {
        setGithubToken(null);
        setGithubUser(null);
        tokenStore.remove(GH_TOKEN_KEY);
        tokenStore.remove(GH_USER_KEY);
    }, []);

    return { githubToken, githubUser, isPolling, deviceFlow, codeCopied, startDeviceFlow, copyCode, signOut };
}
