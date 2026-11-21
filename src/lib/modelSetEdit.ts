/** System model sets are copied into a private set before edits are saved. */

export function shouldCloneSystemModelSet(
  id: string,
  systemSetIds: ReadonlySet<string>,
): boolean {
  return systemSetIds.has(id);
}

export function clonedSystemModelSetName(name: string): string {
  return name.startsWith("My ") ? name : `My ${name}`;
}
