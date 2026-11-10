import { describe, expect, it } from "vitest";
import { Brain } from "lucide-react";

import { groupContainsPath, isSidebarNavGroup, type SidebarNavEntry } from "@/lib/sidebarNav";

const group: SidebarNavEntry = {
  label: "Learning Hub",
  icon: Brain,
  children: [
    { to: "/brain", label: "Brain", icon: Brain },
    { to: "/playbooks", label: "My Playbooks", icon: Brain },
    { to: "/lessons", label: "Lessons", icon: Brain },
  ],
};

const leaf: SidebarNavEntry = { to: "/library", label: "Library", icon: Brain };

describe("isSidebarNavGroup", () => {
  it("identifies a group entry", () => {
    expect(isSidebarNavGroup(group)).toBe(true);
  });

  it("identifies a leaf entry", () => {
    expect(isSidebarNavGroup(leaf)).toBe(false);
  });
});

describe("groupContainsPath", () => {
  it("matches when the path is one of the group's children", () => {
    expect(isSidebarNavGroup(group) && groupContainsPath(group, "/playbooks")).toBe(true);
  });

  it("matches nested child routes", () => {
    expect(isSidebarNavGroup(group) && groupContainsPath(group, "/lessons/123")).toBe(true);
  });

  it("does not match an unrelated path", () => {
    expect(isSidebarNavGroup(group) && groupContainsPath(group, "/library")).toBe(false);
  });
});
