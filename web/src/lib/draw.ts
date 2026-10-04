/**
 * The Fair Draw, byte for byte as defined in docs/contract/draw.md. Used by the "verify the draw"
 * page (and by the demo-mode mock, so its proofs are real ones). WebCrypto only.
 */
const encoder = new TextEncoder();

export function bytesToHex(bytes: Uint8Array): string {
  let out = "";
  for (const b of bytes) out += b.toString(16).padStart(2, "0");
  return out;
}

export function hexToBytes(hex: string): Uint8Array<ArrayBuffer> {
  if (hex.length % 2 !== 0 || /[^0-9a-f]/i.test(hex)) throw new Error("not a hex string");
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i += 1) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

async function sha256Hex(data: Uint8Array<ArrayBuffer>): Promise<string> {
  return bytesToHex(new Uint8Array(await crypto.subtle.digest("SHA-256", data)));
}

/** `seed_commit` = SHA-256 of the 32 raw seed bytes (not of the hex text). */
export function seedCommit(seedHex: string): Promise<string> {
  return sha256Hex(hexToBytes(seedHex));
}

/** `entry_set_hash` = SHA-256 of the sorted ids joined by "\n" (plain byte order). */
export function entrySetHash(publicIds: readonly string[]): Promise<string> {
  return sha256Hex(encoder.encode([...publicIds].sort(byteOrder).join("\n")));
}

export function byteOrder(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Rank key per id: hex(HMAC-SHA256(seed bytes, drop_id + "|" + user_public_id)). */
export async function rankKeys(
  seedHex: string,
  dropId: string,
  publicIds: readonly string[],
): Promise<Map<string, string>> {
  const key = await crypto.subtle.importKey(
    "raw",
    hexToBytes(seedHex),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const keys = new Map<string, string>();
  for (const id of publicIds) {
    const mac = await crypto.subtle.sign("HMAC", key, encoder.encode(`${dropId}|${id}`));
    keys.set(id, bytesToHex(new Uint8Array(mac)));
  }
  return keys;
}

/** Ascending by rank key (rank 1 first); ties by id. */
export async function rankIds(
  seedHex: string,
  dropId: string,
  publicIds: readonly string[],
): Promise<string[]> {
  const keys = await rankKeys(seedHex, dropId, publicIds);
  return [...publicIds].sort(
    (a, b) => byteOrder(keys.get(a) ?? "", keys.get(b) ?? "") || byteOrder(a, b),
  );
}

export interface ProofInput {
  drop_id: string;
  seed: string;
  seed_commit: string;
  entry_set_hash: string;
  eligible_public_ids: readonly string[] | null;
  ranked_public_ids: readonly string[] | null;
}

export interface ProofCheck {
  id: "commit" | "entry_set" | "ranking";
  label: string;
  ok: boolean;
  detail: string;
}

/** Re-runs the draw in this browser and compares each published value. */
export async function verifyProof(proof: ProofInput): Promise<ProofCheck[]> {
  const eligible = proof.eligible_public_ids ?? [];
  const ranked = proof.ranked_public_ids ?? [];

  const commit = await seedCommit(proof.seed);
  const setHash = await entrySetHash(eligible);
  const recomputed = await rankIds(proof.seed, proof.drop_id, eligible);
  const firstDiff = recomputed.findIndex((id, i) => id !== ranked[i]);
  const rankingOk = recomputed.length === ranked.length && firstDiff === -1;

  return [
    {
      id: "commit",
      label: "The seed matches the commitment published before registration opened",
      ok: commit === proof.seed_commit,
      detail: `SHA-256(seed) = ${commit}`,
    },
    {
      id: "entry_set",
      label: "The list of entrants matches the hash frozen when registration closed",
      ok: setHash === proof.entry_set_hash,
      detail: `SHA-256(sorted ids) = ${setHash}`,
    },
    {
      id: "ranking",
      label: `Re-running the draw for ${eligible.length.toLocaleString()} entrants gives the same order`,
      ok: rankingOk,
      detail: rankingOk
        ? "Every rank recomputed here equals the published rank."
        : `First difference at rank ${firstDiff + 1}.`,
    },
  ];
}
