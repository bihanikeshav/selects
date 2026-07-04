import { BrowserRouter, Route, Routes } from "react-router-dom";
import BurstCull from "./views/BurstCull";
import Clusters from "./views/Clusters";
import ClusterDetail from "./views/ClusterDetail";
import Persons from "./views/Persons";
import PersonDetail from "./views/PersonDetail";
import Search from "./views/Search";
import Stories from "./views/Stories";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<BurstCull />} />
        <Route path="/clusters" element={<Clusters />} />
        <Route path="/clusters/:tag" element={<ClusterDetail />} />
        <Route path="/stories" element={<Stories />} />
        <Route path="/search" element={<Search />} />
        <Route path="/persons" element={<Persons />} />
        <Route path="/persons/:id" element={<PersonDetail />} />
      </Routes>
    </BrowserRouter>
  );
}
