import React from "react";
import { Route, Router, Switch, useParams } from "react-router-dom";
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

function ItemEditorRoute({ workloadClient }: { workloadClient: WorkloadClientAPI }) {
    const { itemObjectId } = useParams<{ itemObjectId: string }>();
    return <AgentHubLayout workloadClient={workloadClient} itemObjectId={itemObjectId} />;
}

export function App({ history, workloadClient }: AppProps) {
    return <Router history={history}>
        <Switch>
            {/* AgentHub — multi-agent orchestration dashboard */}
            <Route path="/agent-hub">
                <AgentHubLayout workloadClient={workloadClient} />
            </Route>

            {/* Item editor route — Fabric navigates here when opening an existing item */}
            <Route path="/sample-workload-editor/:itemObjectId">
                <ItemEditorRoute workloadClient={workloadClient} />
            </Route>

            {/* Default: redirect to AgentHub */}
            <Route path="/">
                <AgentHubLayout workloadClient={workloadClient} />
            </Route>
        </Switch>
    </Router>;
}