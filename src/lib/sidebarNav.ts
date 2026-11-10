import type { LucideIcon } from "lucide-react";

export type SidebarNavLeaf = {
  to: string;
  label: string;
  icon: LucideIcon;
};

export type SidebarNavGroup = {
  label: string;
  icon: LucideIcon;
  children: SidebarNavLeaf[];
};

export type SidebarNavEntry = SidebarNavLeaf | SidebarNavGroup;

export function isSidebarNavGroup(entry: SidebarNavEntry): entry is SidebarNavGroup {
  return "children" in entry;
}

export function groupContainsPath(group: SidebarNavGroup, path: string): boolean {
  return group.children.some((child) => path.startsWith(child.to));
}
