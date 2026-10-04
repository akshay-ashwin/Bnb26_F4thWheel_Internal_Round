import { useEffect, useState } from "react";

import { api } from "../../api/client";
import type { Drop } from "../../api/types";
import { navigate } from "../../app/router";
import { copy } from "../user/copy";

export function HowItWorks({ dropId }: { dropId: string | null }) {
  const [drop, setDrop] = useState<Drop | null>(null);
  const t = copy.howItWorks;

  useEffect(() => {
    if (!dropId) return;
    api.drop(dropId).then(setDrop, () => setDrop(null));
  }, [dropId]);

  return (
    <article className="space-y-5">
      <h1 className="text-2xl font-semibold">{t.title}</h1>
      <ol className="list-decimal space-y-3 pl-5 text-slate-700">
        {t.steps.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
      <p className="break-words rounded-md bg-slate-100 px-3 py-2 font-mono text-xs">{t.formula}</p>
      <p className="font-medium">{t.sameChance}</p>
      {drop && (
        <div className="space-y-2 text-sm">
          <p>{drop.seed ? t.proof : t.notRevealed}</p>
          <p className="break-all font-mono text-xs">seed_commit: {drop.seed_commit}</p>
          {drop.seed && (
            <>
              <p className="break-all font-mono text-xs">seed: {drop.seed}</p>
              <a className="underline" href={`/api/drops/${drop.id}/draw-proof`}>
                {copy.proofLink}
              </a>
            </>
          )}
        </div>
      )}
      {dropId && (
        <button
          type="button"
          className="text-sm underline"
          onClick={() => navigate(`/drop/${dropId}`)}
        >
          {t.back}
        </button>
      )}
    </article>
  );
}
