/** Shared Enter-to-send decision logic for composers with Shift+Enter newline support. */

export function shouldSubmitOnEnter(params: {
  key: string;
  shiftKey: boolean;
  isComposing: boolean;
  loading: boolean;
  text: string;
}): boolean {
  if (params.key !== "Enter" || params.shiftKey) return false;
  if (params.isComposing) return false;
  if (params.loading) return false;
  return params.text.trim().length > 0;
}
