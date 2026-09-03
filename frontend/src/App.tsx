import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { libraryStatus } from "./api/client";
import { warmupSearch } from "./api/search2";
import Rail from "./components/Rail";
import TitleBar from "./components/TitleBar";
import BurstCull from "./views/BurstCull";
import Libraries from "./views/Libraries";
import Onboarding from "./views/Onboarding";

const BestOf = lazy(() => import("./views/BestOf"));
const Calibrate = lazy(() => import("./views/Calibrate"));
const CalibrateDashboard = lazy(() => import("./views/CalibrateDashboard"));
const Clusters = lazy(() => import("./views/Clusters"));
const ClusterDetail = lazy(() => import("./views/ClusterDetail"));
const Curated = lazy(() => import("./views/Curated"));
const Dedup = lazy(() => import("./views/Dedup"));
const MapView = lazy(() => import("./views/Map"));
const Persons = lazy(() => import("./views/Persons"));
const PersonDetail = lazy(() => import("./views/PersonDetail"));
const Search = lazy(() => import("./views/Search"));
const Stories = lazy(() => import("./views/Stories"));
const Videos = lazy(() => import("./views/Videos"));

/**
 * Ask the backend whether any library exists. If none does, bounce the user
 * to onboarding (unless they're already there or on /libraries). Re-checks
 * when the path changes so a later empty-library state still gates.
 *
 * When a library already exists, leave /onboarding alone — the user may be
 * adding a second library, or sitting on an in-progress index.
 */
function OnboardingGate() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    let cancelled = false;
    libraryStatus()
      .then((s) => {
        if (cancelled) return;
        const allowed = ["/onboarding", "/libraries"];
        if (s.needs_onboarding && !allowed.includes(location.pathname)) {
          navigate("/onboarding", { replace: true });
        }
      })
      .catch(() => {
        /* backend unreachable — leave the app as-is */
      });
    return () => {
      cancelled = true;
    };
  }, [location.pathname, navigate]);

  return null;
}

/** Legacy `/clusters/:tag` bookmarks keep the tag (and `?source=`). */
function LegacyClusterTagRedirect() {
  const { tag = "" } = useParams();
  const [sp] = useSearchParams();
  const q = sp.toString();
  return <Navigate to={`/cull/clusters/${encodeURIComponent(tag)}${q ? `?${q}` : ""}`} replace />;
}

/** Legacy `/persons/:id` bookmarks keep the person id. */
function LegacyPersonIdRedirect() {
  const { id = "" } = useParams();
  return <Navigate to={`/people/${id}`} replace />;
}

function SearchEngineWarmup() {
  useEffect(() => {
    warmupSearch().catch(() => {});
  }, []);
  return null;
}

function RouteFallback() {
  return (
    <div className="app">
      <Rail />
      <div className="workspace page-skeleton" aria-busy="true">
        <div className="skeleton-grid">
          {Array.from({ length: 12 }, (_, i) => (
            <div key={i} className="skeleton-tile" />
          ))}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <TitleBar />
      <OnboardingGate />
      <SearchEngineWarmup />
      <Suspense fallback={<RouteFallback />}>
        <Routes>
        <Route path="/onboarding" element={<Onboarding />} />
        <Route path="/libraries" element={<Libraries />} />
        {/* Cull mode — three sub-views */}
        <Route path="/" element={<Navigate to="/cull" replace />} />
        <Route path="/cull" element={<BurstCull />} />
        <Route path="/cull/clusters" element={<Clusters />} />
        <Route path="/cull/clusters/:tag" element={<ClusterDetail />} />
        <Route path="/cull/stories" element={<Stories />} />

        {/* Curated mode — liked grid; clusters/stories stay on these routes */}
        <Route path="/curated" element={<Curated />} />
        <Route path="/curated/clusters" element={<Clusters />} />
        <Route path="/curated/clusters/:tag" element={<ClusterDetail />} />
        <Route path="/curated/stories" element={<Stories />} />

        {/* Cross-cutting (independent of mode) */}
        <Route path="/people" element={<Persons />} />
        <Route path="/people/:id" element={<PersonDetail />} />
        <Route path="/map" element={<MapView />} />
        <Route path="/search" element={<Search />} />
        <Route path="/duplicates" element={<Dedup />} />
        <Route path="/videos" element={<Videos />} />
        <Route path="/best/:facet/:value" element={<BestOf />} />
        <Route path="/calibrate" element={<Calibrate />} />
        <Route path="/calibrate/dashboard" element={<CalibrateDashboard />} />

        {/* Legacy redirects so old bookmarks don't 404 */}
        <Route path="/clusters" element={<Navigate to="/cull/clusters" replace />} />
        <Route path="/clusters/:tag" element={<LegacyClusterTagRedirect />} />
        <Route path="/stories" element={<Navigate to="/cull/stories" replace />} />
        <Route path="/persons" element={<Navigate to="/people" replace />} />
        <Route path="/persons/:id" element={<LegacyPersonIdRedirect />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
