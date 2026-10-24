import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { get, query } from "../api";
import { atLeast, useAuth } from "../auth";
import { Badge, ErrorBox, Loading, Pager, show, useAsync, when } from "../components/ui";
import type { ExtractionSummary, Page, SchemaSummary } from "../types";

export function Extractions() {
  const { session } = useAuth();
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(params.entries());
  const [draft, setDraft] = useState(filters);
  const schemas = useAsync(() => get<SchemaSummary[]>("/api/v1/extraction/schemas"), []);
  const results = useAsync(
    () =>
      get<Page<ExtractionSummary>>(
        `/api/v1/extractions${query({
          q: filters.q,
          schema_id: filters.schema_id,
          status: filters.status,
          review_status: filters.review_status,
          submitted_by: filters.submitted_by,
          limit: 25,
          offset: filters.offset ?? 0,
        })}`,
      ),
    [params.toString()],
  );

  function apply(event: FormEvent) {
    event.preventDefault();
    setParams(Object.fromEntries(Object.entries(draft).filter(([k, v]) => v && k !== "offset")));
  }

  const set = (key: string) => (e: { target: { value: string } }) => setDraft({ ...draft, [key]: e.target.value });

  return (
    <section>
      <h1>Extraction results</h1>
      <form className="filters" onSubmit={apply} role="search">
        <label>
          Search
          <input placeholder="file name or any extracted value" value={draft.q ?? ""} onChange={set("q")} />
        </label>
        <label>
          Type
          <select value={draft.schema_id ?? ""} onChange={set("schema_id")}>
            <option value="">any</option>
            {(schemas.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Result
          <select value={draft.status ?? ""} onChange={set("status")}>
            <option value="">any</option>
            <option value="complete">complete</option>
            <option value="needs_review">needs review</option>
            <option value="unclassified">unclassified</option>
          </select>
        </label>
        <label>
          Review
          <select value={draft.review_status ?? ""} onChange={set("review_status")}>
            <option value="">any</option>
            <option value="pending">pending</option>
            <option value="in_review">in review</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
            <option value="auto_accepted">auto accepted</option>
          </select>
        </label>
        {atLeast(session?.role, "viewer") ? (
          <label>
            Submitted by
            <input value={draft.submitted_by ?? ""} onChange={set("submitted_by")} />
          </label>
        ) : null}
        <button className="primary" type="submit">
          Apply
        </button>
      </form>
      <ErrorBox error={results.error} title="Could not search" />
      {results.loading && !results.data ? <Loading /> : null}
      {results.data ? (
        <>
          <table className="list">
            <thead>
              <tr>
                <th>Document</th>
                <th>Type</th>
                <th>Result</th>
                <th>Review</th>
                <th>Values</th>
                <th>Extracted</th>
              </tr>
            </thead>
            <tbody>
              {results.data.items.map((r) => (
                <tr key={`${r.document_id}/${r.layout_run}`}>
                  <td>
                    <Link to={`/documents/${r.document_id}`}>{r.filename ?? r.document_id.slice(0, 12)}</Link>
                    <div className="muted small">{r.submitted_by}</div>
                  </td>
                  <td>{r.schema_id ?? "—"}</td>
                  <td>
                    <Badge value={r.status} />
                  </td>
                  <td>
                    <Badge value={r.review_status} />
                  </td>
                  <td className="small">
                    {Object.entries(r.preview).map(([k, v]) => (
                      <div key={k}>
                        <span className="muted">{k}:</span> {show(v)}
                      </div>
                    ))}
                  </td>
                  <td>{when(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {results.data.items.length === 0 ? <p className="muted">Nothing matches.</p> : null}
          <Pager page={results.data} onOffset={(offset) => setParams({ ...filters, offset: String(offset) })} />
        </>
      ) : null}
    </section>
  );
}
