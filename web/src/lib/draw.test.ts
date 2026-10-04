import { describe, expect, it } from "vitest";

import { entrySetHash, rankIds, seedCommit, verifyProof } from "./draw";

// Reference values computed independently with Node's `crypto` (createHash / createHmac),
// following docs/contract/draw.md.
const SEED = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff";
const DROP = "6f1c2a9e-4b1d-4c7a-9a55-0d2f6b1e7a01";
const IDS = ["a", "b", "c", "d"].map((c) => c.repeat(26));
const COMMIT = "4773d12e2371bb935b9a0f5439b4a1c3ad3f2414b86980f8418d1cfabdfadfef";
const SET_HASH = "4e79f555c45c83e2e36028033a587f3b91982e0d3eb38d3dafe8a183d7b0485e";
const RANKED = ["b", "a", "c", "d"].map((c) => c.repeat(26));

describe("fair draw", () => {
  it("hashes the raw seed bytes for the commitment", async () => {
    expect(await seedCommit(SEED)).toBe(COMMIT);
  });

  it("hashes the sorted ids, whatever order they arrive in", async () => {
    expect(await entrySetHash(IDS)).toBe(SET_HASH);
    expect(await entrySetHash([...IDS].reverse())).toBe(SET_HASH);
  });

  it("ranks by HMAC(seed, drop|id) ascending", async () => {
    expect(await rankIds(SEED, DROP, IDS)).toEqual(RANKED);
  });

  const proof = {
    drop_id: DROP,
    seed: SEED,
    seed_commit: COMMIT,
    entry_set_hash: SET_HASH,
    eligible_public_ids: IDS,
    ranked_public_ids: RANKED,
  };

  it("passes all three checks for an honest proof", async () => {
    const checks = await verifyProof(proof);
    expect(checks.map((c) => [c.id, c.ok])).toEqual([
      ["commit", true],
      ["entry_set", true],
      ["ranking", true],
    ]);
  });

  it("fails the commitment check if the seed was swapped", async () => {
    const checks = await verifyProof({ ...proof, seed: SEED.replace("00", "01") });
    expect(checks.find((c) => c.id === "commit")?.ok).toBe(false);
  });

  it("fails the entrant check if someone was added after the freeze", async () => {
    const checks = await verifyProof({
      ...proof,
      eligible_public_ids: [...IDS, "e".repeat(26)],
    });
    expect(checks.find((c) => c.id === "entry_set")?.ok).toBe(false);
  });

  it("fails the ranking check if two winners were swapped", async () => {
    const swapped = [RANKED[1], RANKED[0], RANKED[2], RANKED[3]].filter((x) => x !== undefined);
    const checks = await verifyProof({ ...proof, ranked_public_ids: swapped });
    expect(checks.find((c) => c.id === "ranking")?.ok).toBe(false);
  });
});
