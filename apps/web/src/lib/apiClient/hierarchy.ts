import { apiFetch } from "@/lib/fetcher";

export interface HierarchyEdge {
  winner_class: string;
  loser_class: string;
  wins: number;
  losses: number;
  win_rate: number;
  sample_conflict_ids: string[];
}

export interface HierarchyRankedEntry {
  class: string;
  wins: number;
  losses: number;
  win_rate: number;
  rank: number;
}

export interface HierarchySameClass {
  class: string;
  count: number;
}

export interface HierarchyResponse {
  edges: HierarchyEdge[];
  ranked: HierarchyRankedEntry[];
  resolved_count: number;
  same_class_conflicts: HierarchySameClass[];
  computed_at: string;
}

export async function fetchHierarchy(slug: string): Promise<HierarchyResponse> {
  return apiFetch<HierarchyResponse>(
    `/projects/${encodeURIComponent(slug)}/hierarchy`,
  );
}
