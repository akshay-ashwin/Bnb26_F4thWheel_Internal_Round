import { matchRoute, useLocation } from "./app/router";
import { HowItWorks } from "./features/how-it-works/HowItWorks";
import { copy } from "./features/user/copy";
import { UserJourney } from "./features/user/UserJourney";

export default function App() {
  const route = matchRoute(useLocation());
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-md px-4 py-3 font-semibold">{copy.appName}</div>
      </header>
      <main className="mx-auto max-w-md px-4 py-6">
        {route.name === "drop" && <UserJourney key={route.dropId} dropId={route.dropId} />}
        {route.name === "how" && <HowItWorks dropId={route.dropId} />}
        {route.name === "home" && <p className="text-slate-600">{copy.dropNotFound.body}</p>}
      </main>
    </div>
  );
}
