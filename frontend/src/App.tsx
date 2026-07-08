import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
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
 * On first load, ask the backend whether any library exists. If none does,
 * bounce the user to onboarding (unless they're already there). Runs once.
 */
function OnboardingGate() {
  const navigate = useNavigate();
  const location = useLocation();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (checked) return;
    let cancelled = false;
    libraryStatus()
      .then((s) => {
        if (cancelled) return;
        // Let the user reach the libraries screen to open an existing project
        // instead of being trapped on onboarding.
        const allowed = ["/onboarding", "/libraries"];
        if (s.needs_onboarding && !allowed.includes(location.pathname)) {
          navigate("/onboarding", { replace: true });
        }
      })
      .catch(() => {
        /* backend unreachable — leave the app as-is */
      })
      .finally(() => {
        if (!cancelled) setChecked(true);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
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

        {/* Curated mode — three sub-views */}
        <Route path="/curated" element={<Curated />} />
        {/* Curated is standalone now — its old sub-views live only under Sort. */}
        <Route path="/curated/clusters" element={<Navigate to="/cull/clusters" replace />} />
        <Route path="/curated/clusters/:tag" element={<Navigate to="/cull/clusters" replace />} />
        <Route path="/curated/stories" element={<Navigate to="/cull/stories" replace />} />

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
        <Route path="/clusters/:tag" element={<Navigate to="/cull/clusters" replace />} />
        <Route path="/stories" element={<Navigate to="/cull/stories" replace />} />
        <Route path="/persons" element={<Navigate to="/people" replace />} />
        <Route path="/persons/:id" element={<Navigate to="/people" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
