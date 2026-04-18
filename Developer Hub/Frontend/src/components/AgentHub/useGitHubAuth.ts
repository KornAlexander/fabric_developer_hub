/**
 * Shared GitHub Device Flow authentication hook.
 * Used by both the ChatPanel and the AgentHub.
 */
import { useState, useRef, useEffect, useCallback } from 'react';

declare const process: { env: Record<string, string | undefined> };
const BE = process.env.WORKLOAD_BE_URL || 'http://localhost:5000';

export interface GitHubAuth {
    githubToken: string | null;
    githubUser: string | null;
    isPolling: boolean;
    deviceFlow: { userCode: string; verificationUri: string; deviceCode: string; interval: number; expiresIn: number } | null;
    codeCopied: boolean;
    startDeviceFlow: () => Promise<void>;
    copyCode: () => void;
    signOut: () => void;
}

export function useGitHubAuth(): GitHubAuth {
    const [githubToken, setGithubToken] = useState<string | null>(
        () => sessionStorage.getItem('github_token')
    );
    const [githubUser, setGithubUser] = useState<string | null>(
        () => sessionStorage.getItem('github_user')
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

    const startDeviceFlow = useCallback(async () => {
        try {
            const resp = await fetch(`${BE}/api/github/device-code`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            if (!resp.ok) throw new Error('Device flow start failed');
            const data = await resp.json();
            const flow = {
                userCode: data.user_code,
                verificationUri: data.verification_uri,
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
                        sessionStorage.setItem('github_token', pollData.access_token);
                        if (pollData.github_user) sessionStorage.setItem('github_user', pollData.github_user);
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
        } catch (e) {
            console.error('Device flow error:', e);
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
        sessionStorage.removeItem('github_token');
        sessionStorage.removeItem('github_user');
    }, []);

    return { githubToken, githubUser, isPolling, deviceFlow, codeCopied, startDeviceFlow, copyCode, signOut };
}
