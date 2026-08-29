/**
 * Multi-tenant path utilities
 * Handles path generation for different organizations/tenants
 * Base path: "/" (multiai org)
 * Datacenter path: "/datacenter" (datacenter org)
 */

import { useAuth } from "./auth";

/**
 * Generate full path for navigation
 * @param basePath - The base path without org prefix (e.g., "/chat", "/library")
 * @param orgPath - The org-specific path (e.g., "", "/datacenter")
 * @returns Full path with org prefix (e.g., "/datacenter/chat")
 */
export function withOrgPath(basePath: string, orgPath: string | null): string {
  if (!orgPath) return basePath;
  return `${orgPath}${basePath}`;
}

/**
 * React hook to get path utilities
 * Usage: const { pathTo } = useOrgPath();
 */
export function useOrgPath() {
  const auth = useAuth();
  const orgPath = auth.orgPath || "";

  const pathTo = (path: string): string => withOrgPath(path, orgPath);

  return { pathTo, orgPath };
}

/**
 * Build navigation links for multi-tenant routes
 */
export const getTenantRoutes = (orgPath: string) => ({
  chat: withOrgPath("/chat", orgPath),
  library: withOrgPath("/library", orgPath),
  playbooks: withOrgPath("/playbooks", orgPath),
  modelSets: withOrgPath("/model-sets", orgPath),
  brain: withOrgPath("/brain", orgPath),
  savedVerdicts: withOrgPath("/saved-verdicts", orgPath),
  savedPrompts: withOrgPath("/saved-prompts", orgPath),
  savedDocuments: withOrgPath("/saved-documents", orgPath),
  settings: withOrgPath("/settings", orgPath),
  projects: withOrgPath("/projects", orgPath),
  lessons: withOrgPath("/lessons", orgPath),
});
