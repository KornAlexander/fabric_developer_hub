import React, { createContext, useContext } from 'react';
import { Text, Badge } from '@fluentui/react-components';
import {
    Open16Regular,
    Database24Regular,
    Notebook24Regular,
    DataPie24Regular,
    Flow24Regular,
    Table24Regular,
    Document24Regular,
} from '@fluentui/react-icons';
import { WorkloadClientAPI } from '@ms-fabric/workload-client';
import { buildFabricItemUrl } from '../../utils/fabricItemLinks';

/** Context to pass workloadClient to ItemCard without prop-drilling. */
export const WorkloadClientContext = createContext<WorkloadClientAPI | null>(null);

/** Maps Fabric item types to icons and colors. */
const ITEM_TYPE_META: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
    Lakehouse:   { icon: <Database24Regular />,  color: '#0078d4', label: 'Lakehouse' },
    Notebook:    { icon: <Notebook24Regular />,  color: '#8764b8', label: 'Notebook' },
    Report:      { icon: <DataPie24Regular />,   color: '#e8910d', label: 'Report' },
    Dashboard:   { icon: <DataPie24Regular />,   color: '#e8910d', label: 'Dashboard' },
    DataPipeline:{ icon: <Flow24Regular />,      color: '#00a86b', label: 'Pipeline' },
    SQLEndpoint: { icon: <Table24Regular />,     color: '#005a9e', label: 'SQL Endpoint' },
    Warehouse:   { icon: <Database24Regular />,  color: '#004578', label: 'Warehouse' },
    SemanticModel:{ icon: <Table24Regular />,    color: '#744da9', label: 'Semantic Model' },
    Eventstream: { icon: <Flow24Regular />,      color: '#00b7c3', label: 'Eventstream' },
    KQLDatabase: { icon: <Database24Regular />,  color: '#0078d4', label: 'KQL Database' },
};

const DEFAULT_META = { icon: <Document24Regular />, color: '#605e5c', label: 'Item' };

export interface ItemCardProps {
    workspaceId: string;
    itemId: string;
    displayName: string;
    itemType: string;
    webUrl?: string;
}

export const ItemCard: React.FC<ItemCardProps> = ({ workspaceId, itemId, displayName, itemType, webUrl }) => {
    const meta = ITEM_TYPE_META[itemType] || DEFAULT_META;
    const workloadClient = useContext(WorkloadClientContext);
    const href = webUrl || buildFabricItemUrl(workspaceId, itemType, itemId);

    const handleClick = async (event: React.MouseEvent<HTMLAnchorElement>) => {
        event.preventDefault();

        if (workloadClient) {
            try {
                await workloadClient.navigation.openBrowserTab({ url: href, queryParams: {} });
                return;
            } catch (err) {
                console.error('Open browser tab failed:', err);
            }
        }

        window.open(href, '_blank', 'noopener,noreferrer');
    };

    return (
        <a
            className="item-card"
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            onClick={handleClick}
        >
            <div className="item-card-icon" style={{ color: meta.color }}>
                {meta.icon}
            </div>
            <div className="item-card-info">
                <Text size={200} weight="semibold" className="item-card-name">
                    {displayName}
                </Text>
                <Badge size="small" appearance="outline" color="informative">
                    {meta.label}
                </Badge>
            </div>
            <div className="item-card-open">
                <Open16Regular />
            </div>
        </a>
    );
};

/**
 * Regex to match item card markers: [[item:WS_ID|ITEM_ID|NAME|TYPE]]
 * Used by renderMessageWithCards to parse AI responses.
 */
const ITEM_CARD_RE = /\[\[item:([0-9a-f-]+)\|([0-9a-f-]+)\|([^|]+)\|([^|\]]+)(?:\|([^\]]+))?\]\]/gi;
const PREVIEW_RE = /\[\[preview:([0-9a-f-]+)\|([0-9a-f-]+)\|([^|]+)\|([^|\]]+)(?:\|([^\]]+))?\]\]/gi;

function stripPreviewMarkers(content: string): string {
    return content.replace(PREVIEW_RE, '').replace(/\n{3,}/g, '\n\n').trim();
}

/**
 * Parse a message string and return an array of React nodes,
 * replacing [[item:...]] markers with <ItemCard> components.
 */
export function renderMessageWithCards(content: string): React.ReactNode[] {
    const visibleContent = stripPreviewMarkers(content);
    const nodes: React.ReactNode[] = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    // Reset regex state
    ITEM_CARD_RE.lastIndex = 0;

    while ((match = ITEM_CARD_RE.exec(visibleContent)) !== null) {
        // Text before this match
        if (match.index > lastIndex) {
            nodes.push(visibleContent.slice(lastIndex, match.index));
        }
        nodes.push(
            <ItemCard
                key={`card-${match.index}`}
                workspaceId={match[1]}
                itemId={match[2]}
                displayName={match[3]}
                itemType={match[4]}
                webUrl={match[5]}
            />
        );
        lastIndex = match.index + match[0].length;
    }

    // Remaining text after last match
    if (lastIndex < visibleContent.length) {
        nodes.push(visibleContent.slice(lastIndex));
    }

    return nodes.length > 0 ? nodes : [visibleContent];
}
