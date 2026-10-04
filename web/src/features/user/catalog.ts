import { hashOf } from "../../lib/format";

/**
 * How a drop is presented (artwork, venue, price). The API only knows a drop's name and rules, so
 * these details live in the app: known names get a hand-written card, anything else gets one
 * picked from its name, so drops created in the admin console still look complete.
 */
export interface Presentation {
  title: string;
  subtitle: string;
  category: Category;
  venue: string;
  city: string;
  when: string;
  price: string;
  about: string;
  /** Poster colours: gradient start, gradient end, glow. */
  palette: readonly [string, string, string];
}

export const CATEGORIES = ["Music", "Comedy", "Festival", "Indie"] as const;
export type Category = (typeof CATEGORIES)[number];

const PALETTES: readonly (readonly [string, string, string])[] = [
  ["#4c1d95", "#be185d", "#f472b6"],
  ["#0c4a6e", "#4338ca", "#38bdf8"],
  ["#7c2d12", "#b45309", "#fbbf24"],
  ["#064e3b", "#0f766e", "#34d399"],
  ["#581c87", "#1d4ed8", "#a78bfa"],
  ["#831843", "#9f1239", "#fb7185"],
];

const VENUES: readonly (readonly [string, string])[] = [
  ["Mahalaxmi Lawns", "Pune"],
  ["Phoenix Marketcity Arena", "Pune"],
  ["Royal Palms Amphitheatre", "Pune"],
  ["Balewadi Stadium Grounds", "Pune"],
];

const KNOWN: Record<string, Partial<Presentation>> = {
  "Arijit Singh: Live in Pune": {
    title: "Arijit Singh",
    subtitle: "Live in Pune",
    category: "Music",
    venue: "Mahalaxmi Lawns",
    when: "Sat, 14 Nov · 6:30 PM",
    price: "₹2,499 onwards",
    palette: PALETTES[0],
    about:
      "Three hours, one voice, the songs everyone knows by heart. A full live band and a 360° stage under the open sky.",
  },
  "Sunburn Arena ft. Martin Garrix": {
    title: "Sunburn Arena",
    subtitle: "ft. Martin Garrix",
    category: "Festival",
    venue: "Balewadi Stadium Grounds",
    when: "Fri, 27 Nov · 4:00 PM",
    price: "₹3,000 onwards",
    palette: PALETTES[1],
    about:
      "The arena edition returns with a headline set, full production and a sunset opening line-up.",
  },
  "Zakir Khan: Tathastu": {
    title: "Zakir Khan",
    subtitle: "Tathastu",
    category: "Comedy",
    venue: "Phoenix Marketcity Arena",
    when: "Sun, 8 Nov · 8:00 PM",
    price: "₹999 onwards",
    palette: PALETTES[2],
    about: "Stories from home, told the only way the Sakht Launda can. Ninety minutes, no opener.",
  },
  "Prateek Kuhad: Silhouettes Tour": {
    title: "Prateek Kuhad",
    subtitle: "Silhouettes Tour",
    category: "Indie",
    venue: "Royal Palms Amphitheatre",
    when: "Sat, 3 Oct · 7:00 PM",
    price: "₹1,499 onwards",
    palette: PALETTES[3],
    about: "An intimate evening of new songs and old favourites with a five-piece band.",
  },
  "Indie Under the Stars": {
    title: "Indie Under the Stars",
    subtitle: "Six bands, one night",
    category: "Indie",
    venue: "Royal Palms Amphitheatre",
    when: "Sat, 12 Dec · 5:00 PM",
    price: "₹799 onwards",
    palette: PALETTES[4],
    about:
      "An open-air line-up of six independent acts, food stalls and a late-night acoustic set.",
  },
};

export function presentationOf(name: string): Presentation {
  const hash = hashOf(name);
  const [venue, city] = VENUES[hash % VENUES.length] ?? ["Mahalaxmi Lawns", "Pune"];
  const [title, ...rest] = name.split(/:\s|\s[-–—]\s/);
  const fallback: Presentation = {
    title: title ?? name,
    subtitle: rest.join(" · ") || "Limited seats",
    category: CATEGORIES[hash % CATEGORIES.length] ?? "Music",
    venue,
    city,
    when: "Date announced soon",
    price: "₹999 onwards",
    about: "A limited-seat drop. Every verified person gets one entry and the same chance.",
    palette: PALETTES[hash % PALETTES.length] ?? ["#4c1d95", "#be185d", "#f472b6"],
  };
  return { ...fallback, ...KNOWN[name] };
}
