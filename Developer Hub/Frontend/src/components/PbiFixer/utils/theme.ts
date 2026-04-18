// Shared theme constants — mirrors _ui_components.py

export const FONT_FAMILY = "-apple-system,BlinkMacSystemFont,sans-serif";
export const TEXT_COLOR = "inherit";
export const BORDER_COLOR = "#e0e0e0";
export const ICON_ACCENT = "#FF9500";
export const GRAY_COLOR = "#999";
export const SECTION_BG = "#fafafa";

// Unicode icons for tree nodes
export const ICONS: Record<string, string> = {
  table: "\u{1F4C1}",       // 📁
  column: "\u{1F4CF}",      // 📏
  measure: "\u{1F4D0}",     // 📐
  hierarchy: "\u{1F517}",   // 🔗
  calc_group: "\u{1F4CA}",  // 📊
  calc_item: "\u2022",      // •
  model: "\u{1F4C4}",       // 📄
  report: "\u{1F4CA}",      // 📊
  page: "\u{1F4C4}",        // 📄
  visual: "\u{1F441}",      // 👁
  partition: "\u{1F4CE}",   // 📎
  folder: "\u{1F4C2}",      // 📂
  relationship: "\u2194",   // ↔
};

// Collapse/expand markers
export const EXPANDED = "\u25BC";  // ▼
export const COLLAPSED = "\u25B6"; // ▶

// Indentation per level (4 non-breaking spaces)
export const INDENT = "\u00A0\u00A0\u00A0\u00A0";
