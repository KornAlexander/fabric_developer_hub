/**
 * SideNavContextMenu — right-click menu for a sidebar nav item.
 *
 * Exposes one-off open actions ("Open in new tab", "Replace current
 * tab", "Open in new group") plus a submenu to set the *default*
 * behaviour for that item. Anchored to the cursor position via the
 * Fluent ``MenuPopover`` positioning APIs.
 */

import React from "react";
import {
    Menu,
    MenuTrigger,
    MenuPopover,
    MenuList,
    MenuItem,
    MenuItemRadio,
    MenuDivider,
    type PositioningImperativeRef,
} from "@fluentui/react-components";
import {
    OpenRegular,
    TabAddRegular,
    ArrowSwap20Regular,
    SplitHorizontal20Regular,
    Settings20Regular,
} from "@fluentui/react-icons";
import {
    BEHAVIOUR_SHORT_LABEL,
    NAV_ITEM_LABEL,
    type NavBehaviour,
    type NavItemId,
} from "./navPreferences";

interface Pos { left: number; top: number }

interface SideNavContextMenuProps {
    /** Position on screen where the menu should anchor (clientX/Y). */
    position: Pos | null;
    itemId: NavItemId;
    /** Current default for this item — shown with a radio checkmark in
     *  the "Set default" submenu. */
    currentDefault: NavBehaviour;
    /** Called when the user picks a one-off open action. */
    onOpenAs: (behaviour: NavBehaviour) => void;
    /** Called when the user sets a new default for this item. */
    onSetDefault: (behaviour: NavBehaviour) => void;
    /** Called when the menu should dismiss (click outside, Esc, any pick). */
    onDismiss: () => void;
}

export function SideNavContextMenu({
    position,
    itemId,
    currentDefault,
    onOpenAs,
    onSetDefault,
    onDismiss,
}: SideNavContextMenuProps) {
    const positioningRef = React.useRef<PositioningImperativeRef>(null);
    const virtualAnchorRef = React.useRef<{
        getBoundingClientRect: () => DOMRect;
    }>({
        getBoundingClientRect: () =>
            new DOMRect(position?.left ?? 0, position?.top ?? 0, 1, 1),
    });

    // Update the virtual anchor whenever the position changes so the
    // menu follows the cursor if the user re-triggers quickly.
    React.useEffect(() => {
        if (!position) return;
        virtualAnchorRef.current = {
            getBoundingClientRect: () =>
                new DOMRect(position.left, position.top, 1, 1),
        };
        positioningRef.current?.setTarget(virtualAnchorRef.current);
    }, [position]);

    if (!position) return null;

    return (
        <Menu
            open
            onOpenChange={(_, data) => { if (!data.open) onDismiss(); }}
            checkedValues={{ [`default-${itemId}`]: [currentDefault] }}
            positioning={{
                position: "below",
                align: "start",
                positioningRef,
                target: virtualAnchorRef.current,
            }}
        >
            <MenuTrigger disableButtonEnhancement>
                {/* Menu needs a trigger for type-safety; we render a
                    zero-size placeholder because the real trigger is
                    the parent's right-click. */}
                <span style={{ position: "absolute", width: 0, height: 0 }} />
            </MenuTrigger>
            <MenuPopover>
                <MenuList>
                    <MenuItem
                        icon={<OpenRegular />}
                        onClick={() => { onOpenAs("smart"); onDismiss(); }}
                    >
                        {BEHAVIOUR_SHORT_LABEL["smart"]} "{NAV_ITEM_LABEL[itemId]}"
                    </MenuItem>
                    <MenuItem
                        icon={<TabAddRegular />}
                        onClick={() => { onOpenAs("new-tab"); onDismiss(); }}
                    >
                        {BEHAVIOUR_SHORT_LABEL["new-tab"]}
                    </MenuItem>
                    <MenuItem
                        icon={<ArrowSwap20Regular />}
                        onClick={() => { onOpenAs("replace"); onDismiss(); }}
                    >
                        {BEHAVIOUR_SHORT_LABEL["replace"]}
                    </MenuItem>
                    <MenuItem
                        icon={<SplitHorizontal20Regular />}
                        onClick={() => { onOpenAs("new-group"); onDismiss(); }}
                    >
                        {BEHAVIOUR_SHORT_LABEL["new-group"]}
                    </MenuItem>
                    <MenuDivider />
                    <MenuItem
                        icon={<Settings20Regular />}
                        disabled
                    >
                        Default behaviour for "{NAV_ITEM_LABEL[itemId]}"
                    </MenuItem>
                    {(["smart", "new-tab", "replace", "new-group"] as NavBehaviour[]).map((b) => (
                        <MenuItemRadio
                            key={b}
                            name={`default-${itemId}`}
                            value={b}
                            onClick={() => { onSetDefault(b); onDismiss(); }}
                        >
                            {BEHAVIOUR_SHORT_LABEL[b]}
                        </MenuItemRadio>
                    ))}
                </MenuList>
            </MenuPopover>
        </Menu>
    );
}
