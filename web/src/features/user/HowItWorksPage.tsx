import { Link } from "react-router";

import { Card } from "../../components/ui";

const STEPS = [
  {
    title: "A secret is locked in first",
    text: "Before entries open, the server picks a random secret (the seed) and publishes only its SHA-256 fingerprint. From that moment the secret can't be swapped without everyone noticing.",
  },
  {
    title: "Everyone enters during a window",
    text: "For a few minutes, any verified phone number can enter once. Entering in the first second or the last makes no difference, so there is nothing to race for.",
  },
  {
    title: "The list is frozen, then ranked",
    text: "When the window closes, the list of entrants is fixed and its hash is published. Each entry gets a rank from HMAC-SHA256(seed, drop + your public id). Lowest value is rank 1.",
  },
  {
    title: "Winners get a held seat",
    text: "Ranks 1 to 500 are offered a seat that is reserved for them for a couple of minutes. If someone doesn't claim, the seat moves to the next person on the waitlist, in draw order.",
  },
  {
    title: "The secret is revealed",
    text: "After the draw the seed is published. Anyone can hash it, compare it with the fingerprint from step 1, and re-run the ranking. This site does that for you in your browser.",
  },
];

const COMPARISON = [
  ["Who wins", "Whoever's request lands first", "Decided by a draw anyone can re-check"],
  ["Sending 1,000 requests", "Big advantage", "No advantage: still one entry"],
  ["Being 2 seconds late", "You lose", "Same chance as everyone else"],
  ["More seats than exist", "Depends on the code", "Impossible: one database row per seat"],
];

/** Static explainer, linked from every drop's commitment. */
export function HowItWorksPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-10 px-4 py-10">
      <header>
        <p className="text-brand-400 text-sm font-semibold tracking-widest uppercase">
          How it works
        </p>
        <h1 className="mt-2 text-4xl font-black tracking-tight sm:text-5xl">
          Your chance depends on being a person, not on being fast.
        </h1>
        <p className="text-ink-300 mt-4 text-lg">
          When 50,000 people and 10,000 bots want 500 seats, "first come, first served" just means
          "scripts first". Fair Drop replaces the race with a short entry window and a draw that can
          be proven honest.
        </p>
      </header>

      <ol className="space-y-4">
        {STEPS.map((step, i) => (
          <li key={step.title}>
            <Card className="flex gap-4 p-5">
              <span className="bg-brand-600 flex h-9 w-9 shrink-0 items-center justify-center rounded-full font-bold text-white">
                {i + 1}
              </span>
              <div>
                <h2 className="font-bold">{step.title}</h2>
                <p className="text-ink-300 mt-1 text-sm leading-relaxed">{step.text}</p>
              </div>
            </Card>
          </li>
        ))}
      </ol>

      <section>
        <h2 className="mb-4 text-2xl font-bold tracking-tight">First come vs. fair draw</h2>
        <div className="border-ink-700 overflow-x-auto rounded-3xl border">
          <table className="w-full min-w-[32rem] text-left text-sm">
            <thead className="bg-ink-850 text-ink-400 text-xs uppercase">
              <tr>
                <th className="p-4 font-semibold"></th>
                <th className="p-4 font-semibold">First come</th>
                <th className="text-brand-300 p-4 font-semibold">Fair Drop</th>
              </tr>
            </thead>
            <tbody className="divide-ink-700 divide-y">
              {COMPARISON.map(([what, before, after]) => (
                <tr key={what}>
                  <th scope="row" className="p-4 font-semibold">
                    {what}
                  </th>
                  <td className="text-ink-300 p-4">{before}</td>
                  <td className="p-4">{after}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-2xl font-bold tracking-tight">
          What about bots with many phone numbers?
        </h2>
        <p className="text-ink-300 leading-relaxed">
          Then they have exactly that many entries, and exactly that many chances, the same as that
          many people would. Suspicious sign-ups are not silently removed or down-ranked; if one
          wins, it has to pass a fresh code check before it can claim, and the evidence is shown on
          the organiser's console.
        </p>
        <Link to="/" className="text-brand-400 inline-block font-semibold hover:underline">
          Browse drops →
        </Link>
      </section>
    </div>
  );
}
