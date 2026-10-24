import { Link } from "react-router";

import { get } from "../api";
import { atLeast, useAuth } from "../auth";
import type { ExtractionResult, FieldResult, ReviewItem } from "../types";
import { Badge, ErrorBox, Loading, Notice, show, useAsync, when } from "./ui";

export function ExtractionView({ id, run }: { id: string; run: string }) {
  const { session } = useAuth();
  const base = `/api/v1/documents/${id}/extraction/${run}`;
  const result = useAsync(() => get<ExtractionResult>(base), [base]);
  const canSeeReview = atLeast(session?.role, "viewer");
  const review = useAsync(
    () => (canSeeReview ? get<ReviewItem>(`/api/v1/review/items/${id}/${run}`).catch(() => null) : Promise.resolve(null)),
    [id, run, canSeeReview],
  );
  if (result.error) return <ErrorBox error={result.error} title="Could not load the result" />;
  if (!result.data) return <Loading />;
  const r = result.data;

  return (
    <div>
      <div className="row spread">
        <div>
          <h2>
            {r.schema_id ?? "Unclassified document"} <Badge value={r.status} />
          </h2>
          <p className="muted small">
            Classification {r.classification.status} (score {r.classification.score.toFixed(2)}) · extracted{" "}
            {when(r.created_at)} · run {r.layout_run}
          </p>
        </div>
        <div className="row">
          <a className="button" href={`${base}/export?format=json`} download>
            JSON
          </a>
          <a className="button" href={`${base}/export?format=csv`} download>
            CSV
          </a>
          <a className="button" href={`${base}/export?format=xlsx`} download>
            Excel
          </a>
        </div>
      </div>
      {review.data ? (
        <Notice kind={review.data.status === "approved" || review.data.status === "auto_accepted" ? "ok" : "warn"}>
          Review: <Badge value={review.data.status} />{" "}
          {review.data.decision ? `by ${review.data.decision.actor}. ` : ""}
          <Link to={`/review/${id}/${run}`}>Open in review</Link>
        </Notice>
      ) : null}
      {r.review.needs_review ? (
        <Notice kind="warn">
          Needs a person to check: {r.review.reasons.join(", ").replace(/_/g, " ")}
          {r.review.details.length ? <div className="small">{r.review.details.join("; ")}</div> : null}
        </Notice>
      ) : null}
      {r.source_notes.length ? <p className="muted small">Notes: {r.source_notes.join("; ")}</p> : null}

      <h3>Fields</h3>
      {r.fields.length === 0 ? <p className="muted">No fields: the document type was not recognised.</p> : <FieldsTable fields={r.fields} />}

      {r.tables.map((t) => (
        <div key={t.name}>
          <h3>
            Table {t.name} <Badge value={t.status} />
          </h3>
          {t.issues.length ? <p className="muted small">{t.issues.join("; ")}</p> : null}
          {t.records.length ? (
            <div className="scroll">
              <table className="list compact">
                <thead>
                  <tr>
                    <th>#</th>
                    {t.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {t.records.map((rec) => (
                    <tr key={rec.index} className={rec.errors.length ? "flag" : ""}>
                      <td>{rec.index + 1}</td>
                      {t.columns.map((c) => {
                        const cell = rec.values[c];
                        return (
                          <td key={c} title={cell?.raw ? `as printed: ${cell.raw}` : undefined}>
                            {show(cell?.value)}
                            {cell && cell.status !== "extracted" ? <Badge value={cell.status} /> : null}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">No rows.</p>
          )}
        </div>
      ))}

      <h3>Checks</h3>
      {r.checks.length === 0 ? <p className="muted">No checks apply.</p> : null}
      <ul className="checks">
        {r.checks.map((c) => (
          <li key={c.id}>
            <Badge value={c.status} /> <strong>{c.id}</strong>: {c.message}
          </li>
        ))}
      </ul>
      {!r.validation.valid ? (
        <>
          <h3>Validation</h3>
          <ul className="checks">
            {r.validation.issues.map((i, n) => (
              <li key={n}>
                <Badge value="invalid" /> {i.location.join(".")}: {i.message}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

function FieldsTable({ fields }: { fields: FieldResult[] }) {
  return (
    <table className="list">
      <thead>
        <tr>
          <th>Field</th>
          <th>Value</th>
          <th>Status</th>
          <th>Confidence</th>
          <th>Source</th>
        </tr>
      </thead>
      <tbody>
        {fields.map((f) => (
          <tr key={f.name} className={f.status !== "extracted" ? "flag" : ""}>
            <td>
              {f.name}
              {f.required ? <span className="muted small"> (required)</span> : null}
            </td>
            <td>
              {f.value === null ? <span className="muted">not found</span> : <strong>{show(f.value)}</strong>}
              {f.candidates.length > 1 && f.status !== "extracted" ? (
                <div className="muted small">candidates: {f.candidates.map((c) => show(c.value)).join(" | ")}</div>
              ) : null}
              {f.errors.length ? <div className="bad-text small">{f.errors.join("; ")}</div> : null}
            </td>
            <td>
              <Badge value={f.status} />
            </td>
            <td>{f.confidence?.toFixed(2) ?? "—"}</td>
            <td className="small">
              {f.evidence.map((e, i) => (
                <div key={i}>
                  p.{e.page_number}: “{e.text}”
                </div>
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
