import React from "react";
import { Route, Router, Switch } from "react-router-dom";
import { History } from "history";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { AgentHubLayout } from "./components/AgentHub/AgentHubLayout";

interface AppProps {
    history: History;
    workloadClient: WorkloadClientAPI;
}

export interface PageProps {
    workloadClient: WorkloadClientAPI;
    history?: History
}

export interface ContextProps {
    itemObjectId?: string;
    workspaceObjectId?: string
}

export function App({ history, workloadClient }: AppProps) {
    return <Router history={history}>
        <Switch>
            {/* AgentHub — multi-agent orchestration dashboard */}
            <Route path="/agent-hub">
                <AgentHubLayout workloadClient={workloadClient} />
            </Route>

            {/* Legacy item editor route — redirects to AgentHub */}
            <Route path="/sample-workload-editor/:itemObjectId">
                <AgentHubLayout workloadClient={workloadClient} />
            </Route>

            {/* Default: redirect to AgentHub */}
            <Route path="/">
                <AgentHubLayout workloadClient={workloadClient} />
            </Route>
        </Switch>
    </Router>;
}