import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Caption1,
} from "@fluentui/react-components";
import {
    PeopleTeam20Regular,
    ChevronDown16Regular,
    ChevronUp16Regular,
} from "@fluentui/react-icons";
import type { Team, TeamPattern } from "../plan/types";
import { TeamStrip } from "./TeamStrip";
import { OrchCanvas } from "./OrchCanvas";
import { visibleTeam } from "./teamVisibility";

/**
 * Collapsible panel that surfaces the proposed visible-agent graph on
 * the run page. Default state: compact ``TeamStrip`` (one line). When
 * expanded, the full ``OrchCanvas`` is revealed, height-capped at
 * ``min(520px, 42vh)`` with internal scroll so the live log underneath
 * is never pushed off-screen.
 */

interface TeamPanelProps {
    team: Team;
    activeAgentId?: string | null;
}

const PATTERN_KEYS: Record<TeamPattern, string> = {
    supervisor: "Pattern_Supervisor",
    sequential: "Pattern_Sequential",
    network: "Pattern_Network",
    hierarchical: "Pattern_Hierarchical",
    solo: "Pattern_Solo",
    mixed: "Pattern_Mixed",
};

export function TeamPanel({
    team,
    activeAgentId,
}: TeamPanelProps) {
    const { t } = useTranslation();
    const [expanded, setExpanded] = useState(false);
    const displayTeam = visibleTeam(team);

    if (displayTeam.nodes.length === 0) return null;

    const agentCount = displayTeam.nodes.length;
    const patternLabel = t(PATTERN_KEYS[displayTeam.pattern] || PATTERN_KEYS.supervisor);

    return (
        <section className={`team-panel ${expanded ? "team-panel--open" : ""}`}>
            <div className="team-panel__bar">
                <div className="team-panel__title">
                    <PeopleTeam20Regular />
                    <span className="team-panel__title-text">{t("TeamPanel_Title")}</span>
                    <Caption1 className="team-panel__meta">
                        {t("TeamPanel_Meta", { agentCount, patternLabel })}
                    </Caption1>
                </div>
                <div className="team-panel__actions">
                    <Button
                        appearance="subtle"
                        size="small"
                        icon={expanded ? <ChevronUp16Regular /> : <ChevronDown16Regular />}
                        onClick={() => setExpanded((v) => !v)}
                        aria-expanded={expanded}
                        aria-controls="team-panel-body"
                    >
                        {expanded ? t("TeamPanel_Collapse") : t("TeamPanel_Expand")}
                    </Button>
                </div>
            </div>

            <div
                id="team-panel-body"
                className="team-panel__body"
            >
                {expanded ? (
                    <OrchCanvas team={displayTeam} activeAgentId={activeAgentId} />
                ) : (
                    <TeamStrip team={displayTeam} activeAgentId={activeAgentId} />
                )}
            </div>
        </section>
    );
}
