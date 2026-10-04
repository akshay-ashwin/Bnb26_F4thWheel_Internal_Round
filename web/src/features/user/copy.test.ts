import { describe, expect, it } from "vitest";

import { copy } from "./copy";

// Never accuse; never imply that retrying, refreshing or speed helps.
const BANNED = [
  "bot",
  "bots",
  "suspicious",
  "fraud",
  "flagged",
  "try again",
  "retry",
  "refresh",
  "hurry",
  "be quick",
];

/** Every string in the copy tree; functions are called with sample arguments. */
function allStrings(node: unknown, path = "copy"): [string, string][] {
  if (typeof node === "string") return [[path, node]];
  if (typeof node === "function") {
    const fn = node as (...args: unknown[]) => unknown;
    return allStrings(fn(...Array(fn.length).fill(7)), `${path}()`);
  }
  if (Array.isArray(node)) return node.flatMap((v, i) => allStrings(v, `${path}[${i}]`));
  if (node && typeof node === "object")
    return Object.entries(node).flatMap(([k, v]) => allStrings(v, `${path}.${k}`));
  return [];
}

function containsWord(text: string, phrase: string): boolean {
  const escaped = phrase.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ /g, "\\s+");
  return new RegExp(`\\b${escaped}\\b`, "i").test(text);
}

describe("copy principles", () => {
  const strings = allStrings(copy);

  it("collects the whole copy tree", () => {
    expect(strings.length).toBeGreaterThan(50);
  });

  it.each(BANNED)("no user-facing string contains the word %j", (word) => {
    const hits = strings.filter(([, s]) => containsWord(s, word)).map(([p]) => p);
    expect(hits).toEqual([]);
  });

  it("matches whole words only, not substrings", () => {
    expect(containsWord("robot", "bot")).toBe(false);
    expect(containsWord("Bottle", "bot")).toBe(false);
    expect(containsWord("refreshing", "refresh")).toBe(false);
    expect(containsWord("A bot entered", "bot")).toBe(true);
    expect(containsWord("Please Try  again", "try again")).toBe(true);
  });

  it("shows the fairness promise", () => {
    expect(copy.earlyDoesntHelp).toBe("Arriving early doesn't help.");
    expect(copy.commitment("b4f6b1aa9fa8")).toContain("b4f6b1aa9fa8");
  });
});
