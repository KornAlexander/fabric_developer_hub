import {
    AccessToken,
    ActionButton,
    AfterNavigateAwayData,
    ItemJobInstance,
    ItemLikeV2,
    BeforeNavigateAwayData,
    BeforeNavigateAwayResult,
    CancelItemJobParams,
    CancelItemJobResult,
    CloseMode,
    CreateItemParams,
    CreateItemResult,
    DatahubSelectorDialogConfig,
    DatahubSelectorDialogResult,
    DialogType,
    WorkloadAction,
    WorkloadClientAPI,
    WorkloadSettings,
    GetItemResult,
    HandleRequestFailureResult,
    NotificationToastDuration,
    NotificationType,
    OpenItemRecentRunsConfig,
    OpenItemSettingsConfig,
    OpenMode,
    OpenUIResult,
    RunItemJobParams,
    ThemeConfiguration,
    Tokens,
    ExtendedItemTypeV2,
    WorkloadErrorDetails,
    ErrorKind,
    UpdateItemResult,
    OpenBrowserTabParams,
} from "@ms-fabric/workload-client";

import { Dispatch, SetStateAction } from "react";
import { GenericItem } from '../models/AgentHubModel';

import {
    AuthenticationUIRequiredException,
    AuthUIRequired,
    FabricExternalWorkloadError,
} from "../models/WorkloadExceptionsModel";

// --- Notification API


/**
 * Calls the 'notification.open' function from the WorkloadClientAPI to display a notification.
 *
 * @param {string} title - The title of the notification.
 * @param {string} message - The message content of the notification.
 * @param {NotificationType} type - The type of the notification (default: NotificationType.Success).
 * @param {NotificationToastDuration} duration - The duration for which the notification should be displayed (default: NotificationToastDuration.Medium).
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {Dispatch<SetStateAction<string>>} setNotificationId - (Optional) A state setter function to update the notification ID.
 */
export async function callNotificationOpen(
    title: string,
    message: string,
    type: NotificationType = NotificationType.Success,
    duration: NotificationToastDuration = NotificationToastDuration.Medium,
    workloadClient: WorkloadClientAPI,
    setNotificationId?: Dispatch<SetStateAction<string>>) {

    const result = await workloadClient.notification.open({
        notificationType: type,
        title,
        duration,
        message
    });
    if (type == NotificationType.Success && setNotificationId) {
        setNotificationId(result?.notificationId);
    }
}

/**
 * Calls the 'notification.hide' function from the WorkloadClientAPI to hide a specific notification.
 *
 * @param {string} notificationId - The ID of the notification to hide.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {Dispatch<SetStateAction<string>>} setNotificationId - A state setter function to update the notification ID after hiding.
 */
export async function callNotificationHide(
    notificationId: string,
    workloadClient: WorkloadClientAPI,
    setNotificationId: Dispatch<SetStateAction<string>>) {

    await workloadClient.notification.hide({ notificationId });

    // Clear the notification ID from the state to reflect the hidden notification
    setNotificationId('');
}

// --- Panel API

/**
 * Calls the 'panel.open' function from the WorkloadClientAPI to open a panel.
 *
 * @param {string} workloadName - The name of the workload responsible for the panel.
 * @param {string} path - The path or route within the workload to open.
 * @param {boolean} isLightDismiss - Whether the panel can be dismissed by clicking outside (light dismiss).
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callPanelOpen(
    workloadName: string,
    path: string,
    isLightDismiss: boolean,
    workloadClient: WorkloadClientAPI) {

    await workloadClient.panel.open({
        workloadName,
        route: { path },
        options: {
            width: window.innerWidth / 3,
            isLightDismiss
        }
    });
}

/**
 * Calls the 'panel.close' function from the WorkloadClientAPI to close a panel.
 *
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callPanelClose(workloadClient: WorkloadClientAPI) {
    await workloadClient.panel.close({ mode: CloseMode.PopOne });
}

// --- Page API


/**
 * Calls the 'page.open' function from the WorkloadClientAPI to open a new page.
 *
 * @param {string} workloadName - The name of the workload responsible for the page.
 * @param {string} path - The path or route within the workload to open.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callPageOpen(
    workloadName: string,
    path: string,
    workloadClient: WorkloadClientAPI) {

    await workloadClient.page.open({ workloadName, route: { path }, mode: OpenMode.ReplaceAll });
}

// --- Navigation API

/**
 * Calls the 'navigation.navigate' function from the WorkloadClientAPI to navigate to a target (host or workload) and path.
 *
 * @param {T} target - The target location to navigate to ('host' or 'workload').
 * @param {string} path - The path or route to navigate to.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callNavigationNavigate<T extends 'host' | 'workload'>(
    target: T,
    path: string,
    workloadClient: WorkloadClientAPI) {

    await workloadClient.navigation.navigate(target, { path });
}

/**
 * Calls acquire access token from the WorkloadClientAPI.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {string} additionalScopesToConsent - Extra scopes to consent (only provide if you are sure the user is missing a consent)
 * @param {string} claimsForConditionalAccessPolicy - Claims returned from the server indicating that token conversion failed because of some conditional access policy - see https://learn.microsoft.com/en-us/entra/msal/dotnet/acquiring-tokens/web-apps-apis/on-behalf-of-flow#handling-multi-factor-auth-mfa-conditional-access-and-incremental-consent
 * @returns {AccessToken}
 */
export async function callAuthAcquireAccessToken(workloadClient: WorkloadClientAPI, additionalScopesToConsent?: string, claimsForConditionalAccessPolicy?: string, promptFullConsent?: boolean): Promise<AccessToken> {
    return workloadClient.auth.acquireAccessToken({
        additionalScopesToConsent: additionalScopesToConsent?.length > 0 ? additionalScopesToConsent.split(' ') : null,
        claimsForConditionalAccessPolicy: claimsForConditionalAccessPolicy?.length > 0 ? claimsForConditionalAccessPolicy : null,
        promptFullConsent
    });
}

// ───────────────────────────────────────────────────────────────────────
// Cached Fabric-token accessor.
//
// Without this, every page in the Developer Hub (Orchestrator, Mission
// Control, Dashboard, Save dialog, PBI Fixer, …) called
// ``workloadClient.auth.acquireAccessToken`` independently on mount —
// and PBI Fixer + the Save dialog passed ``additionalScopesToConsent``,
// which is precisely what triggers the Fabric host's "Additional
// authentication required" overlay. Result: every tab swap re-prompted
// the user for SSO.
//
// We now keep ONE module-level cache:
//   • If a non-expired token is in memory we return it instantly.
//   • Otherwise we issue a single ``acquireAccessToken`` call and dedupe
//     concurrent callers via an in-flight promise.
//   • Scopes are only sent on the very first call (or whenever the cache
//     is cold) — once the user has consented, subsequent calls do not
//     need to repeat them, so the SDK stays silent.
// ───────────────────────────────────────────────────────────────────────
type CachedToken = { token: string; expires: number };
let _fabricTokenCache: CachedToken | null = null;
let _fabricTokenInflight: Promise<string | undefined> | null = null;
// The first call may need to ask for consent for the union of scopes used
// across the hub (Workspace.Read.All, Item.Read.All, Power BI Dataset/
// Workspace/Report.Read.All). After consent is granted, the token is
// reusable for all of these, so subsequent calls don't need to send them.
const FABRIC_CONSENT_SCOPES: readonly string[] = [
    "https://api.fabric.microsoft.com/Workspace.Read.All",
    "https://api.fabric.microsoft.com/Item.Read.All",
    "https://analysis.windows.net/powerbi/api/Dataset.Read.All",
    "https://analysis.windows.net/powerbi/api/Workspace.Read.All",
    "https://analysis.windows.net/powerbi/api/Report.Read.All",
];
const FABRIC_TOKEN_TIMEOUT_MS = 12_000;

function withFabricTokenTimeout<T>(promise: Promise<T>, label: string): Promise<T> {
    let timeoutId: number | undefined;
    const timeout = new Promise<T>((_, reject) => {
        timeoutId = window.setTimeout(
            () => reject(new Error(`${label} timed out after ${FABRIC_TOKEN_TIMEOUT_MS}ms`)),
            FABRIC_TOKEN_TIMEOUT_MS,
        );
    });
    return Promise.race([promise, timeout]).finally(() => {
        if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    });
}

async function acquireFabricToken(
    workloadClient: WorkloadClientAPI,
    additionalScopesToConsent: string[] | null,
    label: string,
): Promise<string | undefined> {
    const result = await withFabricTokenTimeout(
        workloadClient.auth.acquireAccessToken({
            additionalScopesToConsent,
            claimsForConditionalAccessPolicy: "",
        }),
        label,
    );
    return result?.token;
}

function decodeJwtExp(token: string): number | null {
    try {
        const parts = token.split('.');
        if (parts.length < 2) return null;
        // Base64URL → Base64
        const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
        const pad = b64.length % 4 === 0 ? b64 : b64 + '='.repeat(4 - (b64.length % 4));
        const payload = JSON.parse(atob(pad));
        return typeof payload?.exp === 'number' ? payload.exp * 1000 : null;
    } catch {
        return null;
    }
}

/**
 * Get a Fabric/PowerBI access token, reusing the cached one when still
 * valid (>60 s remaining). Safe to call from any page or tab — concurrent
 * callers share a single in-flight promise, so the workload host's SSO
 * overlay shows AT MOST once per token lifetime (≈1 hour).
 *
 * @param workloadClient SDK client
 * @param forceRefresh   bypass the cache (e.g. after 401)
 */
export async function getFabricTokenCached(workloadClient: WorkloadClientAPI, forceRefresh = false): Promise<string | undefined> {
    const now = Date.now();
    if (!forceRefresh && _fabricTokenCache && _fabricTokenCache.expires - now > 60_000) {
        return _fabricTokenCache.token;
    }
    if (_fabricTokenInflight) return _fabricTokenInflight;

    // Cold cache: include the consent scopes so the FIRST call gets the
    // union of permissions every page in the hub needs. Warm cache (just
    // expired): omit scopes — the user already consented in this session.
    const isColdCache = !_fabricTokenCache;
    _fabricTokenInflight = (async () => {
        try {
            let token = await acquireFabricToken(workloadClient, null, "Fabric token acquisition");
            if (!token && isColdCache) {
                token = await acquireFabricToken(
                    workloadClient,
                    [...FABRIC_CONSENT_SCOPES],
                    "Fabric token consent acquisition",
                );
            }
            if (token) {
                const exp = decodeJwtExp(token) ?? (Date.now() + 50 * 60_000);
                _fabricTokenCache = { token, expires: exp };
            }
            return token;
        } catch (err) {
            if (isColdCache) {
                try {
                    const token = await acquireFabricToken(
                        workloadClient,
                        [...FABRIC_CONSENT_SCOPES],
                        "Fabric token consent acquisition",
                    );
                    if (token) {
                        const exp = decodeJwtExp(token) ?? (Date.now() + 50 * 60_000);
                        _fabricTokenCache = { token, expires: exp };
                    }
                    return token;
                } catch (fallbackErr) {
                    console.warn("Fabric token consent acquisition failed:", fallbackErr);
                }
            } else {
                console.warn("Fabric token acquisition failed:", err);
            }
            return undefined;
        } finally {
            _fabricTokenInflight = null;
        }
    })();
    return _fabricTokenInflight;
}

/** Wipe the cached Fabric token (call on sign-out or after a 401). */
export function clearFabricTokenCache(): void {
    _fabricTokenCache = null;
    _fabricTokenInflight = null;
}

/**
 * Calls the 'navigation.onBeforeNavigateAway' function from the WorkloadClientAPI
 * to register a callback preventing navigation to a specific URL.
 *
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callNavigationBeforeNavigateAway(workloadClient: WorkloadClientAPI) {
    // Define a callback function to prevent navigation to URLs containing 'forbidden-url'
    const callback: (event: BeforeNavigateAwayData) => Promise<BeforeNavigateAwayResult> =
        async (event: BeforeNavigateAwayData): Promise<BeforeNavigateAwayResult> => {
            // Return a result indicating whether the navigation can proceed
            return { canLeave: !event.nextUrl?.includes("forbidden-url") };
        };

    // Register the callback using the 'navigation.onBeforeNavigateAway' function
    await workloadClient.navigation.onBeforeNavigateAway(callback);
}

/**
 * Registers a callback to trigger after navigating away from page
 * using the 'navigation.onAfterNavigateAway' function.
 *
 * @param {(event: AfterNavigateAwayData) => Promise<void>} callback - A call back function that executes after navigation away.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callNavigationAfterNavigateAway(
    callback: (event: AfterNavigateAwayData) => Promise<void>,
    workloadClient: WorkloadClientAPI) {
    // Register the callback using the 'navigation.onAfterNavigateAway' function
    await workloadClient.navigation.onAfterNavigateAway(callback);
}

/**
 * Calls the 'navigation.openBrowserTab' function from the WorkloadClientAPI to navigate to a url in a new tab.
 *
 * @param {string} path - The path or route to navigate to.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function CallOpenInNewBrowserTab(
    path: string,
    workloadClient: WorkloadClientAPI) {
    try {
        var params: OpenBrowserTabParams = {
            url: path,
            queryParams: {
                key1: "value1",
            }
        }
        await workloadClient.navigation.openBrowserTab(params);
    } catch (err) {
        console.error(err);
    }
}

// --- Action API

/**
 * Registers a callback to be invoked when a workload action is triggered
 * using the 'action.onAction' function.
 *
 * @param {(action: WorkloadAction<unknown>) => Promise<unknown>} callback - The callback function to handle the action.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callActionOnAction(
    callback: (action: WorkloadAction<unknown>) => Promise<unknown>,
    workloadClient: WorkloadClientAPI) {

    await workloadClient.action.onAction(callback);
}


/**
 * Calls the 'action.execute' function from the WorkloadClientAPI to execute a specific action in a workload.
 *
 * @param {string} actionName - The name of the action to execute.
 * @param {string} workloadName - The name of the workload where the action should be executed.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callActionExecute(
    actionName: string,
    workloadName: string,
    workloadClient: WorkloadClientAPI) {

    await workloadClient.action.execute({ action: actionName, workloadName })
}

// --- Dialog API

/**
 * Calls the 'dialog.open' function from the WorkloadClientAPI to open a dialog.
 *
 * @param {string} workloadName - The name of the workload responsible for the dialog.
 * @param {string} path - The path or route within the workload to open.
 * @param {number} width - The width of the dialog.
 * @param {number} height - The height of the dialog.
 * @param {boolean} hasCloseButton - Whether the dialog should have a close button.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * 
 * @returns
 * @param {OpenUIResult} result of the dialog
 */
export async function callDialogOpen(
    workloadName: string,
    path: string,
    width: number,
    height: number,
    hasCloseButton: boolean,
    workloadClient: WorkloadClientAPI) {

    return await workloadClient.dialog.open({
        dialogType: DialogType.IFrame,
        route: { path },  // Specify the path within the workload and queryParams
        workloadName,
        options: {
            width,
            height,
            hasCloseButton
        }
    });
}

// --- Datahub API

/**
 * Calls the 'datahub.openDialog' function from the WorkloadClientAPI to open a OneLake data hub dialog to select Lakehouse item(s).
 * 
 * @param {ExtendedItemTypeV2[]} supportedTypes - The item types supported by the datahub dialog.
 * @param {string} dialogDescription - The sub-title of the datahub dialog
 * @param {boolean} multiSelectionEnabled - Whether the datahub dialog supports multi selection of datahub items
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {boolean} workspaceNavigationEnabled - Whether the datahub dialog supports workspace navigation bar or not.
 */
export async function callDatahubOpen(
    supportedTypes: ExtendedItemTypeV2[],
    dialogDescription: string,
    multiSelectionEnabled: boolean,
    workloadClient: WorkloadClientAPI,
    workspaceNavigationEnabled: boolean = true): Promise<GenericItem> {

    const datahubConfig: DatahubSelectorDialogConfig = {
        supportedTypes: supportedTypes,
        multiSelectionEnabled: multiSelectionEnabled,
        dialogDescription: dialogDescription,
        workspaceNavigationEnabled: workspaceNavigationEnabled,
        // not in use in the regular selector, but required to be non-empty for validation
        hostDetails: {
            experience: 'agenthub', // Change this to reflect your team's process, e.g., "Build notebook" 
            scenario: 'agenthub-action', // Adjust this to the specific action, e.g., "Select Lakehouse" 
        }
    };

    const result: DatahubSelectorDialogResult = await workloadClient.datahub.openDialog(datahubConfig);
    if (!result.selectedDatahubItem) {
        return null;
    }

    const selectedItem = result.selectedDatahubItem[0];
    const { itemObjectId, workspaceObjectId } = selectedItem;
    const { displayName, description } = selectedItem.datahubItemUI;
    // The runtime sometimes puts a numeric enum into ``itemType`` — the
    // official string-typed field is ``fabricItemType``. Prefer that,
    // then fall back to a legacy ``itemType``, and finally drop anything
    // that isn't a proper non-empty identifier string so downstream code
    // doesn't surface "12" in tooltips.
    const uiAny = selectedItem.datahubItemUI as unknown as Record<string, unknown>;
    const rawType = uiAny.fabricItemType ?? uiAny.itemType;
    const safeType = typeof rawType === "string" && /^[A-Za-z][A-Za-z0-9]*$/.test(rawType)
        ? rawType
        : undefined;
    return {
        id: itemObjectId,
        workspaceId: workspaceObjectId,
        type: safeType,
        displayName,
        description
    };
}

/**
 * Calls the 'dialog.open' function from the WorkloadClientAPI to open a message box dialog.
 *
 * @param {string} title - The title of the message box.
 * @param {string} content - The content or message of the message box.
 * @param {string[]} actionButtonsNames - Names of the action buttons to display in the message box.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @returns {string} - Name of the clicked button
 */
export async function callDialogOpenMsgBox(
    title: string,
    content: string,
    actionButtonsNames: string[],
    workloadClient: WorkloadClientAPI,
    link?: string): Promise<string> {

    // Create an array of ActionButton objects based on the provided action button names
    const actionButtons: ActionButton[] = actionButtonsNames.map(name => ({ name, label: name }));
    const result = await workloadClient.dialog.open({
        dialogType: DialogType.MessageBox,
        messageBoxOptions: {
            title,
            content,
            link: link ? {
                    url: link,
                    label: link
                }
            : undefined,
            actionButtons
        }
    });
    return result.value?.clickedButton;
}

/**
 * Calls the 'dialog.close' function from the WorkloadClientAPI to close a dialog.
 *
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {CloseMode} mode - (Optional) The mode specifying how the dialog should be closed.
 */
export async function callDialogClose(
    workloadClient: WorkloadClientAPI,
    mode?: CloseMode,
    data?: unknown) {

    await workloadClient.dialog.close({ mode, data });
}

// --- Error Handling API

/**
 * Calls the 'errorHandling.openErrorDialog' function from the WorkloadClientAPI to open an error dialog.
 *
 * @param {string} errorMessage - The error message to display in the error dialog.
 * @param {string} title - The title of the error dialog.
 * @param {string} statusCode - The status code associated with the error.
 * @param {string} stackTrace - The stack trace information of the error.
 * @param {string} requestId - The unique request ID related to the error.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callErrorHandlingOpenDialog(
    errorMessage: string,
    title: string,
    statusCode: string,
    stackTrace: string,
    requestId: string,
    workloadClient: WorkloadClientAPI) {

    await workloadClient.errorHandling.openErrorDialog({
        errorMsg: errorMessage,
        errorOptions: {
            title,
            statusCode,
            stackTrace,
            requestId,
            errorTime: Date().toString() // Set the timestamp of the error
        },
        kind: ErrorKind.Error
    });
}

/**
 * Calls the 'errorHandling.handleRequestFailure' function from the WorkloadClientAPI to handle request failures.
 *
 * @param {string} errorMessage - The error message associated with the request failure.
 * @param {number} statusCode - The status code of the failed request.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callErrorHandlingRequestFailure(
    errorMessage: string,
    statusCode: number,
    workloadClient: WorkloadClientAPI) {

    // the handleRequestFailure API handles MFA errors coming from Fabric Backend. 
    // Such errors are identified by the inclusion of the below text inside the 'body'.
    const errorCodeMFA = "AdalMultiFactorAuthRequiredErrorCode";

    const result: HandleRequestFailureResult = await workloadClient.errorHandling.handleRequestFailure({ status: statusCode, body: errorMessage + errorCodeMFA });
    callDialogOpenMsgBox("Request Failure handling", `Failure has ${result.handled ? "" : "NOT"} been handled by Fabric`, [], workloadClient);
}

// --- Item CRUD Api

/**
 * Calls the 'itemCrud.createItem function from the WorkloadClientAPI, creating an Item in Fabric
 *
 * @param {string} workspaceObjectId - WorkspaceObjectId where the item will be created
 * @param {string} itemType - Item type, as registered by the BE 
 * @param {string} displayName - Name of the item
 * @param {string} description - Description of the item (can be seen in item's Settings in Fabric)
 * @param {T} workloadPayload - Additional metadata payload for the item (e.g., selected Lakehouse details).
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @returns {GetItemResult} - A wrapper for the item's data, after it has already been saved
 */
export async function callItemCreate<T>(
    workspaceObjectId: string,
    itemType: string,
    displayName: string,
    description: string,
    workloadPayload: T,
    workloadClient: WorkloadClientAPI): Promise<GenericItem> {
    const params: CreateItemParams = {
        workspaceObjectId,
        payload: {
            itemType,
            displayName,
            description,
            workloadPayload: JSON.stringify(workloadPayload),
            payloadContentType: "InlineJson",
        }
    };

    try {
        const result: CreateItemResult = await workloadClient.itemCrud.createItem(params);
        return {
            id: result.objectId,
            workspaceId: workspaceObjectId,
            type: itemType,
            displayName,
            description,
            createdBy: result.createdByUser.name,
            createdDate: result.createdDate,
            lastModifiedBy: result.modifiedByUser.name,
            lastModifiedDate: result.lastUpdatedDate
        };
    }
    catch (exception) {
        console.error(`Failed to create item: ${exception}`);
        throw exception;
    }
}

/**
 * Calls the 'itemCrud.getItem function from the WorkloadClientAPI
 * The result contains data both from Fabric and from the ISV's backend, if configured
 * 
 * @param {string} objectId - The ObjectId of the item to fetch
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {boolean} isRetry - Indicates that the call is a retry
 * @returns {GetItemResult} - A wrapper for the item's data
 */
export async function callItemGet(objectId: string, workloadClient: WorkloadClientAPI, isRetry?: boolean): Promise<GetItemResult> {
    try {
        const item: GetItemResult = await workloadClient.itemCrud.getItem({ objectId });
        return item;
    } catch (exception: any) {
        // T1: dev-loaded workload items (and items in workspaces that the
        // host cannot resolve) make `itemCrud.getItem` reject with an
        // exception that has no useful `errorCode`/`message` payload.
        // Routing that through `handleException` surfaces a noisy
        // "Could not handle exception: undefined" modal that blocks the
        // entire workload UI on every tab open. We intentionally swallow
        // those — callers already handle a null/throw result gracefully
        // (see `ItemContext.tsx` and `index.ui.tsx`'s `agenthub.tab.onInit`).
        const code = exception?.error?.message?.code;
        const msg  = exception?.error?.message?.value || exception?.message;
        if (!code && !msg) {
            console.warn('[AgentHub] callItemGet returned no usable error for ObjectID %s; returning null', objectId);
            return null as unknown as GetItemResult;
        }
        console.error('Failed locating item with ObjectID %s', objectId, exception);
        return await handleException(exception, workloadClient, isRetry, false /* isDirectWorkloadCall */, callItemGet, objectId);
    }
}

/**
 * Calls the 'itemCrud.updateItem function from the WorkloadClientAPI
 * 
 * @param {string} objectId - The ObjectId of the item to update
 * @param {T|undefined} - Additional metadata payload for the item (e.g., selected Lakehouse details).
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {boolean} isRetry - Indicates that the call is a retry
 * @returns {GetItemResult} - A wrapper for the item's data
 */
export async function callItemUpdate<T>(
    objectId: string,
    payloadData: T | undefined,
    workloadClient: WorkloadClientAPI,
    isRetry?: boolean): Promise<UpdateItemResult> {

    let payloadString: string;
    if (payloadData) {
        payloadString = JSON.stringify(payloadData);
    }
 
    try {
        return await workloadClient.itemCrud.updateItem({
            objectId,
            etag: undefined,
            payload: { workloadPayload: payloadString, payloadContentType: "InlineJson" }
        });
    } catch (exception) {
        console.error(`Failed updating Item ${objectId}`, exception);
        return await handleException(exception, workloadClient, isRetry, false /* isDirectWorkloadCall */, callItemUpdate, objectId, payloadData);
    }
}

/**
 * Calls the 'itemCrud.deleteItem function from the WorkloadClientAPI
 * 
 * @param {string} objectId - The ObjectId of the item to delete
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {boolean} isRetry - Indicates that the call is a retry
 */
export async function callItemDelete(
    objectId: string,
    workloadClient: WorkloadClientAPI,
    isRetry?: boolean): Promise<boolean> {
    try {
        const result = await workloadClient.itemCrud.deleteItem({ objectId });
        return result.success;
    } catch (exception) {
        console.error('Failed deleting Item %s', objectId, exception);
        return await handleException(exception, workloadClient, isRetry, false /* isDirectWorkloadCall */, callItemDelete, objectId);
    }
}

// --- Item Jobs related Api

/**
 * Calls the 'itemSchedule.runItemJob' function from the WorkloadClientAPI, starting item job execution
 *
 * @param {string} objectId - The ObjectId of the item which will run the job.
 * @param {string} jobType - The job type to run.
 * @param {string} jobPayload - Payload to be sent as part of the job
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {boolean} showNotification - show pop-up notification.
 * @param {boolean} isRetry - Indicates that the call is a retry
 * @returns {ItemJobInstance} - The executed job instance metadata.
 */
export async function callRunItemJob(
    objectId: string,
    jobType: string,
    jobPayload: string,
    showNotification: boolean = false,
    workloadClient: WorkloadClientAPI,
    isRetry?: boolean): Promise<ItemJobInstance> {

    const params: RunItemJobParams = {
        itemObjectId: objectId,
        itemJobType: jobType,
        payload: { jobPayloadJson: jobPayload }
    };

    try {
        const result: ItemJobInstance = await workloadClient.itemSchedule.runItemJob(params);
        if (showNotification) {
            callNotificationOpen(
                `${result.itemJobType} execution has begun.`,
                `Job instance ID: ${result.itemJobInstanceId}.`,
                NotificationType.Success,
                NotificationToastDuration.Medium,
                workloadClient);
        }

        return result;
    } catch (exception) {
        console.error(`Failed running item job ${jobType} for item ${objectId}`, exception);
        return await handleException(exception, workloadClient, isRetry, false /* isDirectWorkloadCall */, callRunItemJob, objectId, jobType, jobPayload, showNotification);
    }
}

/**
 * Calls the 'itemSchedule.cancelItemJob' function from the WorkloadClientAPI, canceling item job execution
 *
 * @param {string} objectId - The ObjectId of the item which will run the job.
 * @param {string} jobInstanceObjectId - The Id of the job instance
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {boolean} showNotification - show pop-up notification.
 * @param {boolean} isRetry - Indicates that the call is a retry
 * @returns {CancelItemJobParams} - The executed job instance metadata.
 */
export async function callCancelItemJob(
    objectId: string,
    jobInstanceObjectId: string,
    showNotification: boolean = false,
    workloadClient: WorkloadClientAPI,
    isRetry?: boolean): Promise<CancelItemJobResult> {

    const params: CancelItemJobParams = {
        itemObjectId: objectId,
        jobInstanceId: jobInstanceObjectId,
    };

    try {
        const result: CancelItemJobResult = await workloadClient.itemSchedule.cancelItemJob(params);
        if (showNotification) {
            const success = result.success;
            const notificationMessage = success
                ? `Job instance ID: ${jobInstanceObjectId} for item: ${objectId} was canceled successfully`
                : `Failed to cancel job instance ID: ${jobInstanceObjectId} for item: ${objectId} `;

            callNotificationOpen(
                'Cancel Job result',
                notificationMessage,
                success ? NotificationType.Success : NotificationType.Error,
                NotificationToastDuration.Medium,
                workloadClient);
        }

        return result;
    }
    catch (exception) {
        console.error(`Failed to cancel job instance ID: ${jobInstanceObjectId} for item: ${objectId}`, exception);
        return await handleException(exception, workloadClient, isRetry, false /* isDirectWorkloadCall */, callCancelItemJob, objectId, jobInstanceObjectId, showNotification);
    }
}

/**
 * Calls the 'itemRecentRuns.open' function from the WorkloadClientAPI, opening the shared UI component displaying recent runs of item jobs.
 *
 * @param {ItemLikeV2} item - The item for which we want to display recent job runs.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @returns {OpenUIResult} - The result of the UI operation.
 */
export async function callOpenRecentRuns(
    item: ItemLikeV2,
    workloadClient: WorkloadClientAPI): Promise<OpenUIResult> {

    const config: OpenItemRecentRunsConfig = {
        item: item
    };

    try {
        const result: OpenUIResult = await workloadClient.itemRecentRuns.open(config);
        return result;
    }
    catch (exception) {
        console.error(`Failed to open recent run for item:`, item, exception);
    }

    return null;
}

// --- Workload data plane API
// (Sample calculator endpoints from the original Fabric workload sample
// were removed during the sample-workload cleanup pass.)

// --- Theme API

/**
 * Calls the 'theme.get' function from the WorkloadClientAPI to retrieve the current Fabric Theme configuration.
 *
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @returns {Promise<ThemeConfiguration>} - The retrieved theme configuration.
 */
export async function callThemeGet(workloadClient: WorkloadClientAPI): Promise<ThemeConfiguration> {
    return await workloadClient.theme.get();
}

function tokensToFormattedString(tokens: Tokens): string {
    return Object.entries(tokens)
        .map(([tokenName, tokenValue]) => `${tokenName}: ${tokenValue}`)
        .join(',\r\n');
}

export function themeToView(theme: ThemeConfiguration): string {
    return `Theme name: ${theme.name},\r\n Tokens: ${tokensToFormattedString(theme.tokens)}`;
}


/**
 * Calls the 'theme.onChange' function from the WorkloadClientAPI to register a callback for theme change events.
 *
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callThemeOnChange(workloadClient: WorkloadClientAPI) {
    // Define a callback function to be invoked when the theme changes
    const callback: (theme: ThemeConfiguration) => void =
        (_: ThemeConfiguration): void => {
            // No-op: the Fabric host fires this callback frequently;
            // we don't need to log it.
        };
    await workloadClient.theme.onChange(callback);
}


// --- Settings API

/**
 * Calls the 'settings.get' function from the WorkloadClientAPI to retrieve the current workload settings.
 *
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @returns {Promise<WorkloadSettings>} - The retrieved workload settings.
 */
export async function callSettingsGet(workloadClient: WorkloadClientAPI): Promise<WorkloadSettings> {
    return await workloadClient.settings.get();
}

export async function callLanguageGet(workloadClient: WorkloadClientAPI): Promise<string> {
    const settings = await callSettingsGet(workloadClient);
    return settings.currentLanguageLocale;
}

export function settingsToView(settings: WorkloadSettings): string {
    return [`Instance ID: ${settings.instanceId}`, `Host Origin: ${settings.workloadHostOrigin}`, `Current Language Locale: ${settings.currentLanguageLocale}`, `API URI: ${settings.apiUri}`].join('\r\n');
}

/**
 * Calls the 'settings.onChange' function from the WorkloadClientAPI to register a callback for settings change events.
 *
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 */
export async function callSettingsOnChange(workloadClient: WorkloadClientAPI, changeLang: (language: string) => void) {
    // Define a callback function to be invoked when workload settings change
    const callback: (settings: WorkloadSettings) => void =
        (ws: WorkloadSettings): void => {
            {
                changeLang(ws.currentLanguageLocale);
            };
        };
    await workloadClient.settings.onChange(callback);
}

/**
 * Calls the 'itemSettings.open' function from the WorkloadClientAPI, opening the settings pane shared UI component for the item.
 *
 * @param {ItemLikeV2} item - The item for which we want to show the settings pane.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {string} selectedSettingId - The ID of the tab we want to show. If no ID is passed, the item settings panel will open in the 'About' Tab.
 * @returns {OpenUIResult} - The result of the UI operation.
 */
export async function callOpenSettings(
    item: ItemLikeV2,
    workloadClient: WorkloadClientAPI,
    selectedSettingId?: string): Promise<OpenUIResult> {

    const config: OpenItemSettingsConfig = {
        item,
        selectedSettingId
    };

    try {
        const result: OpenUIResult = await workloadClient.itemSettings.open(config)
        return result;
    }
    catch (exception) {
        console.error(`Failed to open settings for item:`, item, exception);
    }

    return null;
}


/**
 * Calls workload API GetLastResult
 * 
 * @param {string} workloadBEUrl - The URL of the workload backend.
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {string} itemObjectId - the item object id.
 * @param {boolean} isRetry - Indicates that the call is a retry
 */
export async function getLastResult(workloadBEUrl: string, workloadClient: WorkloadClientAPI, itemObjectId: string, isRetry?: boolean): Promise<string> {
    const accessToken: AccessToken = await callAuthAcquireAccessToken(workloadClient);
    const response: Response = await fetch(`${workloadBEUrl}/${itemObjectId}/getLastResult`, { method: `GET`, headers: { 'Authorization': 'Bearer ' + accessToken.token } });
    const responseBody: string = await response.text();
    if (!response.ok) {
        // Handle non-successful responses here
        console.error(`Error get getLastResult API: ${responseBody}`);
        return await handleException(
            responseBody,
            workloadClient,
            isRetry,
            /* isDirectWorkloadCall */ true,
            getLastResult,
            workloadBEUrl);
    }
    return responseBody;
}

/**
 * Calls workload API isOneLakeSupported
 * 
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {string} workspaceObjectId - the workspace object id.
 * @param {string} itemObjectId - the item object id.
 * @param {boolean} isRetry - Indicates that the call is a retry
 */
export async function isOneLakeSupported(workloadBEUrl: string, workloadClient: WorkloadClientAPI, workspaceObjectId: string, itemObjectId: string, isRetry?: boolean): Promise<boolean> {
    const accessToken: AccessToken = await callAuthAcquireAccessToken(workloadClient);
    const response: Response = await fetch(`${workloadBEUrl}/${workspaceObjectId}/${itemObjectId}/isOneLakeSupported`, { method: `GET`, headers: { 'Authorization': 'Bearer ' + accessToken.token } });
    const responseBody: string = await response.text();
    if (!response.ok) {
        // Handle non-successful responses here
        console.error(`Error get isOneLakeSupported API: ${responseBody}`);
        return await handleException(
            responseBody,
            workloadClient,
            isRetry,
            /* isDirectWorkloadCall */ true,
            isOneLakeSupported,
            workloadBEUrl);
    }
    return responseBody == 'true';
}

/**
 * Handles errors propagated from workload backend.
 *
 * @param {any} exception - The exception that we need to handle
 * @param {WorkloadClientAPI} workloadClient - An instance of the WorkloadClientAPI.
 * @param {boolean} isRetry - Indicates that the call is a retry
 * @param {boolean} isDirectWorkloadCall - indicates that the error handling is for a data plane call (from the workload frontend directly to the workload backend)
 * @param {Function} action - The action to retry if the error was handled.
 * @param {...any[]} actionArgs - The arguments to pass to the action.
 * @returns {Promise<any>} - Whether the exception was handled or not.
 */
async function handleException(
    exception: any,
    workloadClient: any,
    isRetry: boolean = false,
    isDirectWorkloadCall: boolean = false,
    action: (...args: any[]) => Promise<any>,
    ...actionArgs: any[]
): Promise<any> {
    var parsedException: WorkloadErrorDetails = null;
    if (isDirectWorkloadCall) {
        // exception is the json returned fom the call
        parsedException = JSON.parse(exception);
    } else {
        // exception is a JS object that contains the json returned from the workload BE
        parsedException = parseExceptionErrorResponse(exception);
    }

    // If the error is a FabricExternalWorkloadError and we could parse it, check if we can handle it.
    if ((isDirectWorkloadCall /* data plane */ || exception.error?.message?.code === FabricExternalWorkloadError /* control plane */)
        && parsedException) {
        const errorHandled = await handleWorkloadError(parsedException, workloadClient);
        if (!isRetry && errorHandled ) {
            // error handled, retry the action
            return await action(...actionArgs, workloadClient, true /*isRetry*/);
        }
    }
    
    // error could not be handled, show the error dialog
    let message = parsedException?.Message || "Unknown error occurred";
    const errorCode = parsedException?.ErrorCode ?? exception.error?.message?.code;
    let title = getAdditionalParameterValue(parsedException, "title") ?? `Could not handle exception: ${errorCode}`;

    if (exception.error?.message?.code === "PowerBICapacityValidationFailed") { 
        message = `Your workspace is assigned to invalid capacity.\n` +
                  `Please verify that the workspace has a valid and active capacity assigned, and try again.`;
        title = "Power BI Capacity Validation Failed";
    }
    await callErrorHandlingOpenDialog(
        message,
        title,
        exception.error?.statusCode,
        exception.response?.stackTrace,
        exception.response?.headers?.requestId,
        workloadClient
    );
    return null;
}

function getAdditionalParameterValue(parsedException: WorkloadErrorDetails, parameterName: string): string {
    return parsedException?.MoreDetails?.[0]?.AdditionalParameters?.find(ap => ap.Name == parameterName)?.Value;
}

async function handleWorkloadError(parsedException: WorkloadErrorDetails, workloadClient: WorkloadClientAPI): Promise<boolean> {
    try {
        // handle codes from your choice, the codes are returned from the workload backend.
        switch (parsedException.ErrorCode) {
            case AuthUIRequired: {
                let authenticationUIRequiredException: AuthenticationUIRequiredException = {
                    ClaimsForConditionalAccessPolicy: parsedException.MoreDetails?.[0].AdditionalParameters?.find(ap => ap.Name == "claimsForCondtionalAccessPolicy")?.Value,
                    ErrorMessage: parsedException.Message,
                    ScopesToConsent:  parsedException?.MoreDetails?.[0].AdditionalParameters?.find(ap => ap.Name == "additionalScopesToConsent")?.Value?.split(", ")
                };
                if (authenticationUIRequiredException?.ErrorMessage?.includes("AADSTS65001")) { // consent
                    await workloadClient.auth.acquireAccessToken({additionalScopesToConsent: authenticationUIRequiredException.ScopesToConsent});
                    return true;
                } else { // conditional access policy
                    await workloadClient.auth.acquireAccessToken({claimsForConditionalAccessPolicy: authenticationUIRequiredException.ClaimsForConditionalAccessPolicy});
                    return true;
                }
            }
        }
    } catch {
        console.error("Failed to handle workload error", parsedException);
    }

    return false;
}

function parseExceptionErrorResponse(exception: any): WorkloadErrorDetails {
    const errorResponse = exception?.error?.message?.["pbi.error"]?.parameters?.ErrorResponse;
    if (!errorResponse) {
        return null;
    }
    return JSON.parse(errorResponse);
}
