/** Pure helpers for chat-store refreshAll race protection. */

export type RefreshAuthSnapshot = {
  token: string;
  orgId: string;
};

/**
 * Whether a completed refreshAll may apply its results to React state.
 * Stale generations and auth/org mismatches must not overwrite current state.
 */
export function shouldApplyRefreshResult(args: {
  requestGeneration: number;
  currentGeneration: number;
  requestAuth: RefreshAuthSnapshot;
  currentAuth: RefreshAuthSnapshot | null;
}): boolean {
  if (args.requestGeneration !== args.currentGeneration) return false;
  if (!args.currentAuth) return false;
  return (
    args.currentAuth.token === args.requestAuth.token &&
    args.currentAuth.orgId === args.requestAuth.orgId
  );
}
