import { useEffect, useMemo, useRef, useState } from "react";

import {
  listAllTags,
  listPersonsForFilter,
  search2,
  type PersonEntry,
  type Search2Hit,
  type TagEntry,
} from "../api/search2";
import KbdFooter from "../components/KbdFooter";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";

const DEBOUNCE_MS = 350;

export default function Search() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagDraft, setTagDraft] = useState("");
  const [tags, setTags] = useState<TagEntry[]>([]);
  const [personId, setPersonId] = useState<number | "">("");
  const [persons, setPersons] = useState<PersonEntry[]>([]);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [minAesthetic, setMinAesthetic] = useState<number>(0);
  const [showFilters, setShowFilters] = useState(false);

  const [hits, setHits] = useState<Search2Hit[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);

  const reqId = useRef(0);

  useEffect(() => {
    listPersonsForFilter().then(setPersons).catch(() => setPersons([]));
    listAllTags().then(data => setTags(data.tags)).catch(() => setTags([]));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q]);

  const hasAnyFilter =
    debouncedQ.trim().length > 0 ||
    selectedTags.length > 0 ||
    personId !== "" ||
    dateFrom !== "" ||
    dateTo !== "" ||
    minAesthetic > 0;

  useEffect(() => {
    if (!hasAnyFilter) {
      setHits([]);
      setSearched(false);
      setErr(null);
      return;
    }
    const myReq = ++reqId.current;
    setLoading(true);
    setErr(null);
    search2({
      q: debouncedQ.trim() || undefined,
      tags: selectedTags.length ? selectedTags : undefined,
      person_id: personId === "" ? undefined : personId,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      min_aesthetic: minAesthetic > 0 ? minAesthetic : undefined,
      limit: 150,
    })
      .then(data => {
        if (myReq !== reqId.current) return;
        setHits(data.results);
        setSearched(true);
      })
      .catch(e => {
        if (myReq !== reqId.current) return;
        setErr(String(e));
        setHits([]);
      })
      .finally(() => {
        if (myReq === reqId.current) setLoading(false);
      });
  }, [debouncedQ, selectedTags, personId, dateFrom, dateTo, minAesthetic, hasAnyFilter]);

  function toggleTag(tag: string) {
    setSelectedTags(prev => (prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]));
  }

  const activeFilterCount = useMemo(
    () =>
      (personId !== "" ? 1 : 0) +
      (dateFrom ? 1 : 0) +
      (dateTo ? 1 : 0) +
      (minAesthetic > 0 ? 1 : 0),
    [personId, dateFrom, dateTo, minAesthetic]
  );

  const tagMatches = useMemo(() => {
    const needle = tagDraft.trim().toLowerCase();
    if (!needle) return tags.slice(0, 8);
    return tags
      .filter(t => t.tag.toLowerCase().includes(needle))
      .slice(0, 8);
  }, [tagDraft, tags]);

  function addDraftTag() {
    const raw = tagDraft.trim();
    if (!raw) return;
    const match = tags.find(t => t.tag.toLowerCase() === raw.toLowerCase()) ?? tagMatches[0];
    if (match && !selectedTags.includes(match.tag)) {
      setSelectedTags(prev => [...prev, match.tag]);
    }
    setTagDraft("");
  }

  function clearStructuredFilters() {
    setPersonId("");
    setDateFrom("");
    setDateTo("");
    setMinAesthetic(0);
  }

  const details = loading
    ? "searching..."
    : err
    ? err
    : searched
    ? `${hits.length} result${hits.length === 1 ? "" : "s"}`
    : "type a query, pick a tag, or set a filter";

  return (
    <div className="app">
      <Rail />
      <div
        className="workspace"
        style={{
          display: "grid",
          gridTemplateRows: "auto 1fr auto",
          height: "100vh",
          maxHeight: "100vh",
          overflow: "hidden",
        }}
      >
        <PageHeader
          context="search"
          title="Search"
          subtitle={details}
          actions={
            <div className="search-header-query">
              <input
                type="search"
                value={q}
                onChange={e => setQ(e.target.value)}
                placeholder="Search your photos in plain words — a place, a scene, a moment"
                autoFocus
                className="search-query-input"
              />
              <button
                type="button"
                className={`filter-chip${showFilters ? " filter-chip--active" : ""}`}
                onClick={() => setShowFilters(v => !v)}
              >
                Filters
                {activeFilterCount > 0 && (
                  <span className="filter-chip-count">{activeFilterCount}</span>
                )}
              </button>
            </div>
          }
          controls={
            <div className="search-header-controls">
              <div className="search-chip-row">
                {selectedTags.length === 0 && !q.trim()
                  ? tags.slice(0, 8).map(t => (
                      <button key={t.tag} className="filter-chip" onClick={() => toggleTag(t.tag)}>
                        {t.tag}
                      </button>
                    ))
                  : selectedTags.map(t => (
                      <button
                        key={t}
                        className="filter-chip filter-chip--active"
                        onClick={() => toggleTag(t)}
                        title="Click to remove"
                      >
                        {t}
                      </button>
                    ))}
              </div>

              <div className="search-tag-adder">
                <input
                  type="search"
                  value={tagDraft}
                  onChange={e => setTagDraft(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addDraftTag();
                    }
                  }}
                  list="search-tag-options"
                  placeholder="Add tag..."
                  className="search-tag-input"
                />
                <datalist id="search-tag-options">
                  {tagMatches.map(t => (
                    <option key={t.tag} value={t.tag} />
                  ))}
                </datalist>
                <button className="filter-chip filter-chip--more" type="button" onClick={addDraftTag}>
                  Add tag
                </button>
              </div>

              {showFilters && (
                <div className="search-filter-strip">
                  <label style={filterFieldStyle}>
                    <span style={filterLabelStyle}>Person</span>
                    <select
                      value={personId}
                      onChange={e => setPersonId(e.target.value ? Number(e.target.value) : "")}
                      style={filterInputStyle}
                    >
                      <option value="">Anyone</option>
                      {persons.map(p => (
                        <option key={p.id} value={p.id}>
                          {p.label || `Person ${p.id}`} ({p.photo_count})
                        </option>
                      ))}
                    </select>
                  </label>

                  <label style={filterFieldStyle}>
                    <span style={filterLabelStyle}>From</span>
                    <input
                      type="date"
                      value={dateFrom}
                      onChange={e => setDateFrom(e.target.value)}
                      style={filterInputStyle}
                    />
                  </label>

                  <label style={filterFieldStyle}>
                    <span style={filterLabelStyle}>To</span>
                    <input
                      type="date"
                      value={dateTo}
                      onChange={e => setDateTo(e.target.value)}
                      style={filterInputStyle}
                    />
                  </label>

                  <label style={filterFieldStyle}>
                    <span style={filterLabelStyle}>Min aesthetic ({minAesthetic.toFixed(1)})</span>
                    <input
                      type="range"
                      min={0}
                      max={10}
                      step={0.5}
                      value={minAesthetic}
                      onChange={e => setMinAesthetic(Number(e.target.value))}
                      style={{ width: 140 }}
                    />
                  </label>

                  {activeFilterCount > 0 && (
                    <button
                      type="button"
                      className="filter-chip filter-chip--more"
                      onClick={clearStructuredFilters}
                    >
                      Clear filters
                    </button>
                  )}
                </div>
              )}
            </div>
          }
        />

        <div className="search-results-pane">
          {hasAnyFilter && !loading && !err && hits.length === 0 && (
            <div className="cluster-detail-empty">No photos match this search.</div>
          )}

          {loading && hits.length === 0 && (
            <div className="cluster-detail-empty">searching...</div>
          )}

          {!hasAnyFilter && (
            <div className="cluster-detail-empty">Search from the header to fill this area with photos.</div>
          )}

          <div className="cluster-detail-grid">
            {hits.map((h, i) => (
              <button
                key={h.sha256}
                className="cluster-photo"
                onClick={() => setLightbox(h.sha256)}
                style={{ cursor: "zoom-in" }}
                title={`rank ${i + 1} - score ${h.score.toFixed(3)}${h.tag_hits ? ` - ${h.tag_hits} tag hit${h.tag_hits === 1 ? "" : "s"}` : ""}`}
              >
                <img src={h.thumb_url} alt="" loading="lazy" />
                <span
                  className="cluster-photo-check"
                  style={{
                    background: h.tag_hits > 0 ? "var(--md-primary)" : "rgba(0,0,0,0.55)",
                    color: h.tag_hits > 0 ? "var(--md-on-primary)" : "#fff",
                    fontSize: 11,
                    fontWeight: 500,
                    fontFamily: "var(--font-mono)",
                    width: "auto",
                    minWidth: 24,
                    padding: "0 6px",
                    borderRadius: 12,
                  }}
                >
                  {h.tag_hits > 0 ? `tag x${h.tag_hits}` : h.score.toFixed(2)}
                </span>
              </button>
            ))}
          </div>
        </div>

        <KbdFooter />
      </div>

      {lightbox && (
        <div
          onClick={() => setLightbox(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.92)",
            zIndex: 90,
            display: "grid",
            placeItems: "center",
            cursor: "zoom-out",
          }}
        >
          <img
            src={`/api/preview/${lightbox}`}
            alt=""
            style={{ maxWidth: "94vw", maxHeight: "94vh", boxShadow: "0 12px 60px rgba(0,0,0,0.8)" }}
          />
        </div>
      )}
    </div>
  );
}

const filterFieldStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const filterLabelStyle: React.CSSProperties = {
  fontFamily: "var(--font-display)",
  fontSize: 11,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  color: "var(--md-on-surface-var)",
};

const filterInputStyle: React.CSSProperties = {
  height: 34,
  padding: "0 10px",
  borderRadius: 8,
  border: "1px solid var(--md-outline-var)",
  background: "var(--md-surface)",
  color: "var(--md-on-surface)",
  fontFamily: "var(--font-body)",
  fontSize: 13,
};
