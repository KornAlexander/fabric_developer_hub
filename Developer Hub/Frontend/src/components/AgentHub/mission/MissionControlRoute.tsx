/**
 * Route adapter — renders ``MissionControlPage`` at
 * ``/agent-hub/session/:sessionId`` by reading the session id from
 * route params. The initial-job fields are intentionally empty here:
 * when the user lands on the permalink, ``useMissionStream`` will
 * seed state from the SSE ``run_overview`` frame + fallback
 * ``getSession()`` call. The task-prompt recap falls back to the
 * session summary returned by the backend.
 */

import React from "react";
import { useParams } from "react-router-dom";
import type { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { MissionControlPage } from "./MissionControlPage";

interface MissionControlRouteProps {
    workloadClient: WorkloadClientAPI;
}

export function MissionControlRoute({ workloadClient }: MissionControlRouteProps) {
    const { sessionId } = useParams<{ sessionId: string }>();
    return (
        <MissionControlPage
            workloadClient={workloadClient}
            sessionId={sessionId}
        />
    );
}
