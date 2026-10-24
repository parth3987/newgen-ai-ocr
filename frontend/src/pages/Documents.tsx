import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { get, query } from "../api";
import { atLeast, useAuth } from "../auth";
import { Badge, bytes, ErrorBox, JobProgress, Loading, Pager, useAsync, when } from "../components/ui";
import type { DocumentSummary, Page } from "../types";

const ACTIVE = new Set(["queued", "running"]);

export function Documents() {
  const { session } = useAuth();
  const [params, setParams] = useSearchParams();
  const [draft, setDraft] = useState(() => Object.fromEntries(params.entries()));
  const filters = Object.fromEntries(params.entries());
  const key = params.toString();

  const docs = useAsync(
    () =>
      get<Page<DocumentSummary>>(
        `/api/v1/documents${query({
          q: filters.q,
          status: filters.status,
          submitted_by: filters.submitted_by,
          created_from: filters.from ? `${filters.from}T00:00:00` : undefined,
          created_to: filters.to ? `${filters.to}T23:59:59` : undefined,
          limit: 25,
          offset: filters.offset ?? 0,
        })}`,
      ),
    [key],
    // Refresh while anything shown is still being processed.
    { ms: 3000, keepPolling: (p) => p.items.some((d) => d.latest_job && ACTIVE.has(d.latest_job.status)) },
  );

  function apply(event: FormEvent) {
    event.preventDefault();
    const next = Object.fromEntries(Object.entries(draft).filter(([k, v]) => v && k !== "offset"));
    setParams(next);
  }

  return (
    <section>
      <h1>Documents</h1>
      <form className="filters" onSubmit={apply} role="search">
        <label>
          Search
          <input
            placeholder="file name or document id"
            value={draft.q ?? ""}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          Status
          <select value={draft.status ?? ""} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>
            <option value="">any</option>
            <option value="received">received (queued)</option>
            <option value="ready">ready</option>
            <option value="rejected">rejected</option>
          </select>
        </label>
        {atLeast(session?.role, "viewer") ? (
          <label>
            Submitted by
            <input value={draft.submitted_by ?? ""} onChange={(e) => setDraft({ ...draft, submitted_by: e.target.value })} />
          </label>
        ) : null}
        <label>
          From
          <input type="date" value={draft.from ?? ""} onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
        </label>
        <label>
          To
          <input type="date" value={draft.to ?? ""} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
        </label>
        <button type="submit" className="primary">
          Apply
        </button>
        <button
          type="button"
          onClick={() => {
            setDraft({});
            setParams({});
          }}
        >
          Clear
        </button>
      </form>
      <ErrorBox error={docs.error} title="Could not load documents" />
      {docs.loading && !docs.data ? <Loading /> : null}
      {docs.data ? (
        <>
          {docs.data.items.length === 0 ? (
            <p className="muted">
              No documents match. <Link to="/upload">Upload documents</Link>.
            </p>
          ) : (
            <table className="list">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Status</th>
                  <th>Processing</th>
                  <th>Pages</th>
                  <th>Size</th>
                  <th>Submitted by</th>
                  <th>Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {docs.data.items.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <Link to={`/documents/${d.id}`}>{d.filename ?? d.id.slice(0, 12)}</Link>
                      <div className="muted small mono">{d.id.slice(0, 12)}</div>
                    </td>
                    <td>
                      <Badge value={d.status} />
                      {d.error_code ? <div className="small bad-text">{d.error_code}</div> : null}
                    </td>
                    <td>
                      <JobProgress job={d.latest_job} />
                    </td>
                    <td>{d.page_count ?? "—"}</td>
                    <td>{bytes(d.size_bytes)}</td>
                    <td>{d.submitted_by || "—"}</td>
                    <td>{when(d.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Pager page={docs.data} onOffset={(offset) => setParams({ ...filters, offset: String(offset) })} />
        </>
      ) : null}
    </section>
  );
}
