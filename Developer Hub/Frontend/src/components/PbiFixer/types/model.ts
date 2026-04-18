// Types for Semantic Model Explorer

export interface ColumnInfo {
  dataType: string;
  isHidden: boolean;
  expression: string | null;
  type: string;
  summarizeBy: string;
  displayFolder: string;
  isKey: boolean;
  dataCategory: string;
  sortByColumn: string;
  encodingHint: string;
  isNullable: boolean;
}

export interface MeasureInfo {
  expression: string;
  formatString: string;
  description: string;
  displayFolder: string;
  isHidden: boolean;
}

export interface HierarchyInfo {
  levels: string[];
}

export interface CalcItemInfo {
  expression: string;
  ordinal: number;
}

export interface PartitionInfo {
  name: string;
  sourceType: string;
  expression: string;
}

export interface RelationshipInfo {
  fromTable: string;
  fromColumn: string;
  toTable: string;
  toColumn: string;
  crossFilter: string;
  isActive: boolean;
  multiplicity: string;
  securityFiltering: string;
  relyOnRri: boolean;
}

export interface TableInfo {
  description: string;
  isHidden: boolean;
  type: "Table" | "CalculationGroup" | "CalculatedTable";
  columns: Record<string, ColumnInfo>;
  measures: Record<string, MeasureInfo>;
  hierarchies: Record<string, HierarchyInfo>;
  calcItems: Record<string, CalcItemInfo>;
  partitions: PartitionInfo[];
}

export interface ModelProperties {
  compatibilityLevel: string;
  defaultMode: string;
}

export interface ModelData {
  tables: Record<string, TableInfo>;
  relationships: RelationshipInfo[];
  perspectives: string[];
  modelProperties: ModelProperties;
  datasetName?: string;
}

export type TreeNodeType =
  | "model"
  | "table"
  | "column"
  | "measure"
  | "hierarchy"
  | "calcItem"
  | "partition"
  | "folder"
  | "columnFolder"
  | "relationship"
  | "relationships"
  | "perspectives";
