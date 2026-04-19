/**
 * Global search bar context.
 *
 * The topbar lives in `AgentHubLayout` and its single input is shared across
 * every page. Each page decides how to interpret `query`:
 *
 *   - AgentsPage     → filters agents/skills by name/description/tags.
 *   - DashboardPage  → filters session cards by task/plan/attachments.
 *   - OrchestratorPage → shows a cross-entity quick-results panel
 *                         (sessions, agents, workspaces).
 *
 * `scope` is the active page id and is set by the layout, so consumers can
 * branch on it if they want (e.g. to opt into extra behaviour only on the
 * New Session page).
 */
import React, { createContext, useContext, useMemo, useState, useEffect } from "react";

export type SearchScope = "newsession" | "sessions" | "sessiondetail" | "agents" | "pbifixer" | "other";

interface SearchState {
    query: string;
    setQuery: (q: string) => void;
    scope: SearchScope;
}

const SearchCtx = createContext<SearchState>({
    query: "",
    setQuery: () => { /* no-op default */ },
    scope: "other",
});

interface ProviderProps {
    scope: SearchScope;
    children: React.ReactNode;
}

export function SearchProvider({ scope, children }: ProviderProps) {
    const [query, setQuery] = useState("");

    // Reset the query whenever the active page changes — a filter typed on
    // the Agents page shouldn't silently carry over to Sessions.
    useEffect(() => {
        setQuery("");
    }, [scope]);

    const value = useMemo<SearchState>(
        () => ({ query, setQuery, scope }),
        [query, scope],
    );

    return <SearchCtx.Provider value={value}>{children}</SearchCtx.Provider>;
}

export function useSearch(): SearchState {
    return useContext(SearchCtx);
}

/** Placeholder text shown in the topbar input, per active page. */
export function searchPlaceholderFor(scope: SearchScope): string {
    switch (scope) {
        case "newsession": return "Search sessions, agents, workspaces…";
        case "sessions":   return "Filter sessions on this page…";
        case "agents":     return "Filter agents & skills on this page…";
        case "pbifixer":   return "Search…";
        default:           return "Search Developer Hub…";
    }
}

/**
 * Whether the topbar input acts as an in-page filter (AgentsPage,
 * DashboardPage) vs. a cross-entity search with a results dropdown
 * (OrchestratorPage). Filter mode gets a distinct visual treatment
 * (filter icon, different chrome) so the two behaviours are never
 * confused by the user.
 */
export function isFilterScope(scope: SearchScope): boolean {
    return scope === "sessions" || scope === "agents";
}
