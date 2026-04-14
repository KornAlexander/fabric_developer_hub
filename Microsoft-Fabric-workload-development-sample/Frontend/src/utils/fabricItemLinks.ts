const ITEM_ROUTE_SEGMENTS: Record<string, string> = {
    DataPipeline: 'pipelines',
    Lakehouse: 'lakehouses',
    Notebook: 'notebooks',
    Report: 'reports',
    Dashboard: 'dashboards',
    Warehouse: 'warehouses',
    SemanticModel: 'semanticmodels',
    SQLEndpoint: 'sqlendpoints',
    Eventstream: 'eventstreams',
    KQLDatabase: 'kqldatabases',
    KQLDashboard: 'kqldashboards',
    KQLQueryset: 'kqlquerysets',
    Environment: 'environments',
    SparkJobDefinition: 'sparkjobdefinitions',
    CopyJob: 'copyjobs',
};

function getPortalContext(): { origin: string; searchParams: URLSearchParams } {
    try {
        if (document.referrer) {
            const referrerUrl = new URL(document.referrer);
            return {
                origin: 'https://app.powerbi.com',
                searchParams: referrerUrl.searchParams,
            };
        }
    } catch {
        // Fall back below.
    }

    return {
        origin: 'https://app.powerbi.com',
        searchParams: new URLSearchParams(),
    };
}

function getItemRouteSegment(itemType: string): string {
    if (ITEM_ROUTE_SEGMENTS[itemType]) {
        return ITEM_ROUTE_SEGMENTS[itemType];
    }

    return `${itemType.replace(/([a-z])([A-Z])/g, '$1-$2').replace(/\s+/g, '-').toLowerCase()}s`;
}

export function buildFabricItemUrl(workspaceId: string, itemType: string, itemId: string): string {
    const { origin, searchParams } = getPortalContext();
    const url = new URL(`${origin}/groups/${workspaceId}/${getItemRouteSegment(itemType)}/${itemId}`);

    const ctid = searchParams.get('ctid');
    const experience = searchParams.get('experience');

    if (ctid) {
        url.searchParams.set('ctid', ctid);
    }
    if (experience) {
        url.searchParams.set('experience', experience);
    } else {
        url.searchParams.set('experience', 'fabric-developer');
    }

    return url.toString();
}

export function buildFabricItemPath(workspaceId: string, itemType: string, itemId: string): string {
    return `/groups/${workspaceId}/${getItemRouteSegment(itemType)}/${itemId}`;
}