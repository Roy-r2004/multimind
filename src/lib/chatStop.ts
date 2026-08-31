/** Preserve any draft started while generation was running; otherwise restore the stopped prompt. */
export function composerValueAfterStop(currentValue: string, stoppedPrompt: string): string {
  return currentValue === "" ? stoppedPrompt : currentValue;
}
