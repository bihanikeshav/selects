import { useNavigate, useLocation } from "react-router-dom";

interface StatusRowProps {
  pos?: string;
  keepersCount?: number;
  details?: string;
}

export default function StatusRow({ pos, keepersCount, details }: StatusRowProps) {
  const navigate = useNavigate();
  const { pathname } = useLocation();

  return (
    <div className="status-row">
      {pos && <span className="pos">{pos}</span>}
      {pos && <span className="div">·</span>}
      {keepersCount !== undefined && (
        <>
          <span className="keepers">
            <span className="keepers-dot"></span>
            {keepersCount} keepers so far
          </span>
          <span className="div">·</span>
        </>
      )}
      {details && <span>{details}</span>}

      <div className="status-row-spacer"></div>

      <div className="view-tabs" role="tablist">
        <button
          className={"view-tab" + (pathname === "/" ? " is-active" : "")}
          role="tab"
          aria-selected={pathname === "/"}
          onClick={() => navigate("/")}
        >
          Burst cull
        </button>
        <button
          className={"view-tab" + (pathname === "/clusters" ? " is-active" : "")}
          role="tab"
          aria-selected={pathname === "/clusters"}
          onClick={() => navigate("/clusters")}
        >
          Clusters
        </button>
        <button
          className={"view-tab" + (pathname === "/stories" ? " is-active" : "")}
          role="tab"
          aria-selected={pathname === "/stories"}
          onClick={() => navigate("/stories")}
        >
          Stories
        </button>
      </div>
    </div>
  );
}
