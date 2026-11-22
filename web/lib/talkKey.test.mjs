// node --experimental-strip-types --test lib/talkKey.test.mjs   (in CI: the web job)
import { test } from "node:test";
import assert from "node:assert/strict";
import { startsTalking, stopsTalking } from "./talkKey.ts";

const key = (k, extra = {}) => ({
  key: k, code: k === " " ? "Space" : k === "Enter" ? "Enter" : /^[a-z]$/i.test(k) ? `Key${k.toUpperCase()}` : k,
  ctrlKey: false, metaKey: false, altKey: false, shiftKey: /^[A-Z]$/.test(k), repeat: false, ...extra,
});

test("typing a message with spaces and pressing Enter never starts recording", () => {
  const typed = [..."Book me Friday off, then next Monday too"].map((c) => key(c));
  for (const e of [...typed, key("Enter"), key(" ", { repeat: true }), key("Enter", { repeat: true }), key("Enter", { shiftKey: true })]) {
    assert.equal(startsTalking(e), false, JSON.stringify(e));
  }
});

test("Ctrl+Space starts recording; other modifiers with Space do not", () => {
  assert.equal(startsTalking(key(" ", { ctrlKey: true })), true);
  assert.equal(startsTalking(key(" ", { ctrlKey: true, repeat: true })), false);       // held down: one start only
  for (const m of ["metaKey", "altKey", "shiftKey"]) {
    assert.equal(startsTalking(key(" ", { ctrlKey: true, [m]: true })), false, m);     // Cmd+Space is Spotlight, Option+Space a character
    assert.equal(startsTalking(key(" ", { [m]: true })), false, m);
  }
  assert.equal(startsTalking(key("Enter", { ctrlKey: true })), false);
});

test("letting go of Space or Ctrl stops recording", () => {
  assert.equal(stopsTalking({ code: "Space", key: " " }), true);
  assert.equal(stopsTalking({ code: "ControlLeft", key: "Control" }), true);
  assert.equal(stopsTalking({ code: "KeyA", key: "a" }), false);
});
