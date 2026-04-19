// IMPORTANT: `./logging` must be the very first import — its module
// body silences `console.log/info/debug/trace` and needs to run
// before any peer import (e.g. react-i18next's Locize banner) gets
// to print.
import "./logging";
import { createBrowserHistory } from "history";
import React from "react";
import { createRoot } from 'react-dom/client';

import { FluentProvider } from "@fluentui/react-components";
import { createWorkloadClient, InitParams, ItemTabActionContext } from '@ms-fabric/workload-client';

import { fabricLightTheme } from "./theme";
import { App } from "./App";
import { convertGetItemResultToWorkloadItem } from "./utils";
import { callItemGet } from "./controller/AgentHubController";
import { ItemPayload } from "./models/AgentHubModel";
// Initialize i18next in the UI bundle. Without this, `useTranslation()`
// calls inside the UI (e.g. PlanView) fire react-i18next's
// "NO_I18NEXT_INSTANCE" warning. The auth bundle (index.ts) imports it
// separately for its own flow.
import "./i18n";

export async function initialize(params: InitParams) {
    const workloadClient = createWorkloadClient();

    const history = createBrowserHistory();
    workloadClient.navigation.onNavigate((route) => history.replace(route.targetUrl));
    workloadClient.action.onAction(async function ({ action, data }) {
        switch (action) {
            case 'agenthub.tab.onInit':
                const { id } = data as ItemTabActionContext;
                try{
                    const getItemResult = await callItemGet(
                        id,
                        workloadClient
                    );
                    const item = convertGetItemResultToWorkloadItem<ItemPayload>(getItemResult);
                    return {title: item.displayName};
                } catch (error) {
                    console.error(
                        `Error loading the Item (object ID:${id})`,
                        error
                    );
                    return {};
                }
            case 'agenthub.tab.canDeactivate':
                return { canDeactivate: true };
            case 'agenthub.tab.onDeactivate':
                return {};
            case 'agenthub.tab.canDestroy':
                return { canDestroy: true };
            case 'agenthub.tab.onDestroy':
                return {};
            case 'agenthub.tab.onDelete':
                return {};
            default:
                throw new Error('Unknown action received');
        }
    });
    const root = createRoot(document.getElementById('root'));
    root.render(
        <FluentProvider theme={fabricLightTheme}>
            <App history={history} workloadClient={workloadClient} />
        </FluentProvider>
    );
}
