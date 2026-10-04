import { lazy, Suspense, useEffect } from "react";
import { createBrowserRouter, Link, RouterProvider, useRouteError } from "react-router";

import { Layout } from "../components/Layout";
import { Spinner } from "../components/ui";
import { DropPage } from "../features/user/DropPage";
import { HomePage } from "../features/user/HomePage";
import { HowItWorksPage } from "../features/user/HowItWorksPage";
import { ProofPage } from "../features/user/ProofPage";
import { TicketsPage } from "../features/user/TicketsPage";
import { watchBrowserConnectivity } from "../state/connectivity";

// The console pulls in the charting library; keep it out of the ticket-buyer's bundle.
const AdminGate = lazy(() =>
  import("../features/admin/AdminGate").then((m) => ({ default: m.AdminGate })),
);
const AdminHome = lazy(() =>
  import("../features/admin/AdminHome").then((m) => ({ default: m.AdminHome })),
);
const AdminDrop = lazy(() =>
  import("../features/admin/AdminDrop").then((m) => ({ default: m.AdminDrop })),
);

function PageFallback() {
  return (
    <div className="flex justify-center py-24">
      <Spinner className="text-brand-400 h-6 w-6" />
    </div>
  );
}

/** Error boundary for every route: a render bug shows a way out instead of a blank page. */
function RouteError() {
  const error = useRouteError();
  return (
    <div className="mx-auto max-w-xl px-4 py-24 text-center">
      <h1 className="text-3xl font-black tracking-tight">Something broke on this page</h1>
      <p className="text-ink-400 mt-2 text-sm">
        {error instanceof Error ? error.message : "Unexpected error."} Your entry and seat are safe
        on the server.
      </p>
      <Link to="/" className="text-brand-400 mt-6 inline-block font-medium hover:underline">
        ← Back to drops
      </Link>
    </div>
  );
}

function NotFound() {
  return (
    <div className="mx-auto max-w-xl px-4 py-24 text-center">
      <h1 className="text-3xl font-black tracking-tight">Page not found</h1>
      <Link to="/" className="text-brand-400 mt-6 inline-block font-medium hover:underline">
        ← Back to drops
      </Link>
    </div>
  );
}

const suspended = (node: React.ReactNode) => (
  <Suspense fallback={<PageFallback />}>{node}</Suspense>
);

const router = createBrowserRouter([
  {
    element: <Layout />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "drop/:dropId", element: <DropPage /> },
      { path: "drop/:dropId/proof", element: <ProofPage /> },
      { path: "tickets", element: <TicketsPage /> },
      { path: "how-it-works", element: <HowItWorksPage /> },
      {
        path: "admin",
        element: suspended(<AdminGate />),
        children: [
          { index: true, element: suspended(<AdminHome />) },
          { path: "drop/:dropId", element: suspended(<AdminDrop />) },
        ],
      },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

export default function App() {
  useEffect(() => watchBrowserConnectivity(), []);
  return <RouterProvider router={router} />;
}
