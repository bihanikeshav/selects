import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { libraryStatus } from "./api/client";
import TitleBar from "./components/TitleBar";
import BestOf from "./views/BestOf";
import BurstCull from "./views/BurstCull";
import Calibrate from "./views/Calibrate";
import CalibrateDashboard from "./views/CalibrateDashboard";
import Clusters from "./views/Clusters";
import ClusterDetail from "./views/ClusterDetail";
import Curated from "./views/Curated";
import Dedup from "./views/Dedup";
import Libraries from "./views/Libraries";
import MapView from "./views/Map";
import Onboarding from "./views/Onboarding";
import Persons from "./views/Persons";
import PersonDetail from "./views/PersonDetail";
import Search from "./views/Search";
import Stories from "./views/Stories";
import Videos from "./views/Videos";

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

export default function App() {
  return (
    <BrowserRouter>
      <TitleBar />
      <OnboardingGate />
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
    </BrowserRouter>
  );
}
