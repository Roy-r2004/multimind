/** Model Sets page: which system slugs PATCH in place vs clone-on-save. */

export const IN_PLACE_EDITABLE_SYSTEM_SLUG = "set-7edaefc8";

export function shouldCloneSystemModelSet(
  id: string,
  systemSetIds: ReadonlySet<string>,
): boolean {
  return systemSetIds.has(id) && id !== IN_PLACE_EDITABLE_SYSTEM_SLUG;
}

export function clonedSystemModelSetName(name: string): string {
  return name.startsWith("My ") ? name : `My ${name}`;
}
