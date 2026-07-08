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
import Viewer from "../components/Viewer";

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
  const [lightbox, setLightbox] = useState<number | null>(null);

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
                  <label className="search-filter-field">
                    <span className="search-filter-label">Person</span>
                    <select
                      value={personId}
                      onChange={e => setPersonId(e.target.value ? Number(e.target.value) : "")}
                      className="search-filter-input"
                    >
                      <option value="">Anyone</option>
                      {persons.map(p => (
                        <option key={p.id} value={p.id}>
                          {p.label || `Person ${p.id}`} ({p.photo_count})
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="search-filter-field">
                    <span className="search-filter-label">From</span>
                    <input
                      type="date"
                      value={dateFrom}
                      onChange={e => setDateFrom(e.target.value)}
                      className="search-filter-input"
                    />
                  </label>

                  <label className="search-filter-field">
                    <span className="search-filter-label">To</span>
                    <input
                      type="date"
                      value={dateTo}
                      onChange={e => setDateTo(e.target.value)}
                      className="search-filter-input"
                    />
                  </label>

                  <label className="search-filter-field">
                    <span className="search-filter-label">Min aesthetic ({minAesthetic.toFixed(1)})</span>
                    <input
                      type="range"
                      min={0}
                      max={10}
                      step={0.5}
                      value={minAesthetic}
                      onChange={e => setMinAesthetic(Number(e.target.value))}
                      className="search-filter-range"
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
                onClick={() => setLightbox(i)}
                style={{ cursor: "zoom-in" }}
                title={`rank ${i + 1} - score ${h.score.toFixed(3)}${h.tag_hits ? ` - ${h.tag_hits} tag hit${h.tag_hits === 1 ? "" : "s"}` : ""}`}
              >
                <img src={h.thumb_url} alt="" loading="lazy" />
                {/* Only badge meaningful signals — a raw SigLIP cosine (~0.05)
                    rendered as "0.0" on every tile read as broken. Tag matches
                    are the one badge worth showing. */}
                {h.tag_hits > 0 && (
                  <span
                    className="cluster-photo-check"
                    style={{
                      background: "var(--md-primary)",
                      color: "var(--md-on-primary)",
                      fontSize: 11,
                      fontWeight: 500,
                      fontFamily: "var(--font-mono)",
                      width: "auto",
                      minWidth: 24,
                      padding: "0 6px",
                      borderRadius: 12,
                    }}
                  >
                    {`tag x${h.tag_hits}`}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>

        <KbdFooter />
      </div>

      {lightbox !== null && hits[lightbox] && (
        <Viewer
          items={hits.map((h) => ({ sha256: h.sha256 }))}
          index={lightbox}
          onIndex={setLightbox}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  );
}
