// The keyboard shortcut for voice input: hold Ctrl+Space while the chat box has focus. Space and Enter alone are typing keys (Enter sends), so
// they never start recording. A pure function, so `node --test` checks it in CI (talkKey.test.mjs) without a browser.

export type KeyLike = { code: string; key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean; repeat: boolean };

// keydown: true when this key starts recording
export function startsTalking(e: KeyLike): boolean {
  return e.code === "Space" && e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && !e.repeat;
}

// keyup while recording: letting go of Space or Ctrl stops it
export function stopsTalking(e: Pick<KeyLike, "code" | "key">): boolean {
  return e.code === "Space" || e.key === "Control";
}
