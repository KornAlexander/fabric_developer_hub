// Common types shared across explorers

export interface TreeItem {
  indent: number;
  icon: string;
  label: string;
  key: string;
}

export interface TreeBuildResult {
  options: string[];
  keyMap: Record<string, string>;
}

export interface ScanResult {
  [key: string]: number;
}

export interface ScanDetail {
  fixerName: string;
  description: string;
}

export type ConnectionStatus = "idle" | "connecting" | "connected" | "error";
