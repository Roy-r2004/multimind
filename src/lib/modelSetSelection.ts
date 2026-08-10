/** Default model-set selection for chat (pure helpers, testable). */

export const DEFAULT_MODEL_SET_TITLE = "chafic ultimate model set";

const LEGACY_DEFAULT_SLUG = "referee";

export type ModelSetRef = {
  id: string;
  name: string;
};

export type TurnModelSetRef = {
  model_set_id: string;
  created_at?: string;
};

/** Trim, collapse whitespace, and lowercase for exact title matching. */
export function normalizeModelSetTitle(name: string): string {
  return name.trim().toLowerCase().replace(/\s+/g, " ");
}

/**
 * Find the org-visible default model set by exact normalized title.
 * Duplicate titles: deterministic lowest `id` (localeCompare).
 * Missing: returns null and logs (no throw).
 */
export function findDefaultModelSetId(sets: readonly ModelSetRef[]): string | null {
  const target = normalizeModelSetTitle(DEFAULT_MODEL_SET_TITLE);
  const matches = sets
    .filter((set) => normalizeModelSetTitle(set.name) === target)
    .slice()
    .sort((a, b) => a.id.localeCompare(b.id));

  if (matches.length === 0) {
    console.info(
      `[modelSetSelection] Default model set "${DEFAULT_MODEL_SET_TITLE}" not found; falling back.`,
    );
    return null;
  }

  if (matches.length > 1) {
    console.warn(
      `[modelSetSelection] Multiple model sets titled "${DEFAULT_MODEL_SET_TITLE}"; using id=${matches[0]!.id}.`,
    );
  }

  return matches[0]!.id;
}

/**
 * Resolve active model-set id.
 * - Keep `currentId` when it still exists (manual session choice / valid selection).
 * - Else prefer exact title {@link DEFAULT_MODEL_SET_TITLE}.
 * - Else legacy referee slug / name, then first set.
 */
export function selectExistingModelSetId(
  sets: readonly ModelSetRef[],
  currentId: string,
): string {
  if (currentId && sets.some((set) => set.id === currentId)) return currentId;

  const preferred = findDefaultModelSetId(sets);
  if (preferred) return preferred;

  const legacy =
    sets.find((set) => set.id === LEGACY_DEFAULT_SLUG) ??
    sets.find((set) => normalizeModelSetTitle(set.name).includes("referee"));
  return legacy?.id ?? sets[0]?.id ?? "";
}

/**
 * Model set associated with a chat from its turns (newest by `created_at`).
 * Empty turns → null (caller keeps current / default selection).
 */
export function resolveModelSetIdFromTurns(
  turns: readonly TurnModelSetRef[],
): string | null {
  if (!turns.length) return null;

  const sorted = turns.slice().sort((a, b) => {
    const ta = a.created_at ? Date.parse(a.created_at) : Number.NaN;
    const tb = b.created_at ? Date.parse(b.created_at) : Number.NaN;
    const aOk = Number.isFinite(ta);
    const bOk = Number.isFinite(tb);
    if (aOk && bOk) return tb - ta;
    if (aOk) return -1;
    if (bOk) return 1;
    return 0;
  });

  const id = sorted[0]?.model_set_id?.trim();
  return id || null;
}

/**
 * Resolve the model set for the *next* message in a chat.
 * Prefer the chat-level selection when still available; otherwise fall back to
 * the newest turn's snapshot (legacy chats without chats.model_set_id).
 */
export function resolveNextModelSetId(options: {
  chatModelSetId?: string | null;
  turns: readonly TurnModelSetRef[];
  availableSetIds: readonly string[];
}): string | null {
  const available = new Set(options.availableSetIds);
  const fromChat = options.chatModelSetId?.trim();
  if (fromChat && available.has(fromChat)) return fromChat;

  const fromTurns = resolveModelSetIdFromTurns(options.turns);
  if (fromTurns && available.has(fromTurns)) return fromTurns;
  return null;
}
