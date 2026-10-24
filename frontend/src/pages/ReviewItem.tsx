import { useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError, get, post } from "../api";
import { atLeast, useAuth } from "../auth";
import { Badge, ErrorBox, Loading, Notice, show, useAsync, when } from "../components/ui";
import {
  CORRECTION_REASONS,
  REJECT_REASONS,
  type FieldDecision,
  type HistoryEvent,
  type ReviewedResult,
  type ReviewItem,
} from "../types";

interface Loaded {
  item: ReviewItem;
  result: ReviewedResult;
  history: HistoryEvent[];
}

type Change = Record<string, unknown>;

export function ReviewItemPage() {
  const { id = "", run = "" } = useParams();
  const { session } = useAuth();
  const base = `/api/v1/review/items/${id}/${run}`;
  const data = useAsync<Loaded>(
    async () => {
      const [item, result, history] = await Promise.all([
        get<ReviewItem>(base),
        get<ReviewedResult>(`${base}/result`),
        get<HistoryEvent[]>(`${base}/history`),
      ]);
      return { item, result, history };
    },
    [base],
  );
  const [error, setError] = useState<unknown>(null);
  const [page, setPage] = useState(1);
  const [focus, setFocus] = useState<string | null>(null);

  if (data.error) return <ErrorBox error={data.error} title="Could not load the review item" />;
  if (!data.data) return <Loading />;
  const { item, result, history } = data.data;
  const role = session?.role;
  const open = item.status === "pending" || item.status === "in_review";
  const mine = item.assigned_to === session?.user;
  const canReview = atLeast(role, "reviewer");
  const canApprove = atLeast(role, "approver");
  const editable = open && canReview && mine && item.schema_id !== null;

  async function act(path: string, body: Change) {
    setError(null);
    try {
      await post(`${base}/${path}`, { expected_version: item.version, ...body });
    } catch (e) {
      setError(
        e instanceof ApiError && e.code === "REVIEW_CONFLICT"
          ? new Error(`${e.message}. The item has been reloaded: check it and try again.`)
          : e,
      );
    }
    data.reload();
  }

  const correct = (change: Change) => act("corrections", { changes: [change] });
  const decisions = Object.fromEntries(item.assessment.fields.map((d) => [d.field, d]));

  return (
    <section>
      <p>
        <Link to="/review">← Review queue</Link> · <Link to={`/documents/${id}`}>Document</Link>
      </p>
      <h1>
        {item.schema_id ?? "Unclassified document"} <Badge value={item.status} />
      </h1>
      <p className="muted small">
        Document {id} · run {run} · revision {item.revision} · version {item.version}
        {item.assigned_to ? ` · assigned to ${item.assigned_to}` : ""}
      </p>
      {item.assessment.calibration_id ? null : (
        <Notice kind="warn">No calibration is loaded: no value is verified automatically. Check every value.</Notice>
      )}
      {item.decision ? (
        <Notice kind={item.decision.action === "rejected" ? "warn" : "ok"}>
          {item.decision.action} by {item.decision.actor}
          {item.decision.reason ? ` (${item.decision.reason})` : ""}
          {item.decision.comment ? `: ${item.decision.comment}` : ""}
        </Notice>
      ) : null}
      <ErrorBox error={error} />

      <Actions
        open={open}
        mine={mine}
        canReview={canReview}
        canApprove={canApprove}
        isAdmin={atLeast(role, "admin")}
        assigned={item.assigned_to}
        result={result}
        act={act}
      />

      <div className="split">
        <div>
          <h2>Fields</h2>
          {item.schema_id === null ? <Notice kind="warn">Unclassified: reject it, or extract it again as a type.</Notice> : null}
          <table className="list review">
            <thead>
              <tr>
                <th>Field</th>
                <th>Value</th>
                <th>Source on the page</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(result.fields).map(([name, f]) => {
                const d = decisions[name];
                const correction = item.corrections[`fields.${name}`];
                return (
                  <tr key={name} className={d && !d.verified ? "flag" : ""}>
                    <td>
                      <button type="button" className="link" onClick={() => setFocus(name)} title="Show on the page">
                        {name}
                      </button>
                      {d?.required ? <div className="muted small">required</div> : null}
                    </td>
                    <td>
                      {f.value === null ? <span className="muted">({f.status})</span> : <strong>{show(f.value)}</strong>}
                      <div>
                        <Badge value={f.source} />
                      </div>
                      {correction ? (
                        <div className="muted small">
                          {correction.action} by {correction.actor} ({correction.reason})
                        </div>
                      ) : null}
                      {editable ? <Editor onChange={(c) => void correct({ field: name, ...c })} /> : null}
                    </td>
                    <td>
                      <img
                        className="crop"
                        src={`${base}/crop?field=${encodeURIComponent(name)}`}
                        alt={`Where ${name} was read`}
                        onError={(e) => ((e.target as HTMLImageElement).hidden = true)}
                      />
                    </td>
                    <td>
                      <Decision d={d} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {item.assessment.tables.map((t) => {
            const rows = result.tables[t.table] ?? [];
            const columns = rows[0] ? Object.keys(rows[0]) : [];
            const flagged = new Set(t.cells_flagged);
            const confirmed = item.corrections[`tables.${t.table}`];
            return (
              <div key={t.table}>
                <h2>
                  Table {t.table} {t.verified ? <Badge value="verified" /> : t.reasons.map((r) => <Badge key={r} value={r} />)}
                </h2>
                <div className="scroll">
                  <table className="list compact">
                    <thead>
                      <tr>
                        <th>#</th>
                        {columns.map((c) => (
                          <th key={c}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, i) => (
                        <tr key={i}>
                          <td>{i + 1}</td>
                          {columns.map((c) => (
                            <td key={c} className={flagged.has(`${i}.${c}`) ? "flag" : ""}>
                              {show(row[c])}
                              {flagged.has(`${i}.${c}`) ? (
                                <>
                                  <img
                                    className="crop"
                                    src={`${base}/crop?table=${t.table}&record=${i}&column=${c}`}
                                    alt={`Source of ${c} in row ${i + 1}`}
                                    onError={(e) => ((e.target as HTMLImageElement).hidden = true)}
                                  />
                                  {editable ? (
                                    <Editor onChange={(ch) => void correct({ table: t.table, record: i, column: c, ...ch })} />
                                  ) : null}
                                </>
                              ) : null}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {confirmed ? <p className="muted small">Rows confirmed by {confirmed.actor}</p> : null}
                {editable && t.status === "extracted" && !t.verified && !confirmed ? (
                  <button type="button" onClick={() => void correct({ table: t.table, action: "confirm" })}>
                    Confirm all rows of {t.table}
                  </button>
                ) : null}
              </div>
            );
          })}

          <h2>Checks</h2>
          <ul className="checks">
            {result.checks.map((c) => (
              <li key={c.id}>
                <Badge value={c.status} /> <strong>{c.id}</strong>: {c.message}
              </li>
            ))}
          </ul>
        </div>

        <div className="sticky">
          <h2>Page</h2>
          <div className="row">
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              ←
            </button>
            <span>Page {page}</span>
            <button type="button" onClick={() => setPage(page + 1)}>
              →
            </button>
            {focus ? (
              <span className="muted small">
                highlighting <strong>{focus}</strong>{" "}
                <button type="button" className="link" onClick={() => setFocus(null)}>
                  clear
                </button>
              </span>
            ) : null}
          </div>
          <img
            className="page"
            src={`${base}/pages/${page}${focus ? `?field=${encodeURIComponent(focus)}` : ""}`}
            alt={`Page ${page}${focus ? ` with ${focus} highlighted` : ""}`}
          />
          <p className="muted small">Red: the value's evidence. Blue: other candidates.</p>
          <h2>History</h2>
          <ol className="history">
            {history.map((e) => (
              <li key={e.seq}>
                {when(e.ts)} · {e.actor} · {e.kind}
                {e.location ? ` · ${e.location}: ${show(e.previous_value)} → ${show(e.new_value)}` : ""}
                {e.reason ? ` (${e.reason})` : ""}
                {e.comment ? ` · “${e.comment}”` : ""}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

function Decision({ d }: { d: FieldDecision | undefined }) {
  if (!d) return null;
  const info = [
    d.confidence !== null ? `confidence ${d.confidence.toFixed(2)}` : null,
    d.threshold !== null ? `threshold ${d.threshold.toFixed(2)}` : null,
    d.engine.status !== "unavailable" ? `${d.engine.engine ?? "second engine"}: ${d.engine.status}` : null,
  ].filter(Boolean);
  return (
    <div className="small">
      {d.verified ? <Badge value="verified" /> : null}
      {d.reasons.map((r) => (
        <Badge key={r} value={r} />
      ))}
      {d.checks_failed.map((c) => (
        <Badge key={c} value="failed" title={`check ${c} failed`} />
      ))}
      <div className="muted">{info.join(" · ")}</div>
      {d.details.length ? <div className="muted">{d.details.join("; ")}</div> : null}
    </div>
  );
}

function Editor({ onChange }: { onChange: (change: Change) => void }) {
  const [value, setValue] = useState("");
  const [reason, setReason] = useState<string>("ocr_misread");
  const [comment, setComment] = useState("");
  const extra = { reason, comment: comment || null };
  return (
    <div className="editor">
      <input aria-label="Correct value (as printed)" placeholder="value as printed" value={value} onChange={(e) => setValue(e.target.value)} />
      <select aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}>
        {CORRECTION_REASONS.map((r) => (
          <option key={r} value={r}>
            {r.replace(/_/g, " ")}
          </option>
        ))}
      </select>
      <input aria-label="Comment" placeholder={reason === "other" ? "comment (required)" : "comment"} value={comment} onChange={(e) => setComment(e.target.value)} />
      <button type="button" disabled={!value} onClick={() => onChange({ action: "set", value, ...extra })}>
        Set
      </button>
      <button type="button" onClick={() => onChange({ action: "clear", ...extra, reason: reason === "ocr_misread" ? "value_not_present" : reason })}>
        Not on page
      </button>
      <button type="button" onClick={() => onChange({ action: "confirm" })}>
        Correct as is
      </button>
    </div>
  );
}

function Actions(props: {
  open: boolean;
  mine: boolean;
  canReview: boolean;
  canApprove: boolean;
  isAdmin: boolean;
  assigned: string | null;
  result: ReviewedResult;
  act: (path: string, body: Change) => Promise<void>;
}) {
  const { open, mine, canReview, canApprove, isAdmin, assigned, result, act } = props;
  const [rejectReason, setRejectReason] = useState<string>("illegible");
  const [comment, setComment] = useState("");
  const [ack, setAck] = useState<string[]>([]);
  if (!open) return null;
  const failing = result.checks.filter((c) => c.status === "failed").map((c) => c.id);
  return (
    <div className="panel">
      <div className="row">
        {canReview && !mine && !assigned ? (
          <button type="button" className="primary" onClick={() => void act("claim", {})}>
            Claim to review
          </button>
        ) : null}
        {isAdmin && assigned && !mine ? (
          <button type="button" onClick={() => void act("claim", { force: true })}>
            Take over from {assigned}
          </button>
        ) : null}
        {mine ? (
          <button type="button" onClick={() => void act("release", {})}>
            Release
          </button>
        ) : null}
        {!canReview ? <span className="muted">Your role can read this item but not change it.</span> : null}
      </div>
      {canReview ? (
        <div className="row">
          <select aria-label="Reject reason" value={rejectReason} onChange={(e) => setRejectReason(e.target.value)}>
            {REJECT_REASONS.map((r) => (
              <option key={r} value={r}>
                {r.replace(/_/g, " ")}
              </option>
            ))}
          </select>
          <input aria-label="Comment" placeholder="comment" value={comment} onChange={(e) => setComment(e.target.value)} />
          <button type="button" onClick={() => void act("reject", { reason: rejectReason, comment: comment || null })}>
            Reject document
          </button>
        </div>
      ) : null}
      {canApprove ? (
        <div className="row">
          {failing.map((id) => (
            <label key={id} className="check">
              <input
                type="checkbox"
                checked={ack.includes(id)}
                onChange={(e) => setAck(e.target.checked ? [...ack, id] : ack.filter((a) => a !== id))}
              />
              accept failing check {id}
            </label>
          ))}
          <button
            type="button"
            className="primary"
            disabled={result.unresolved.length > 0}
            onClick={() => void act("approve", { comment: comment || null, acknowledge_failed_checks: ack })}
          >
            Approve
          </button>
        </div>
      ) : null}
      {result.unresolved.length ? (
        <p className="muted small">Still to decide before approval: {result.unresolved.join(", ")}</p>
      ) : null}
    </div>
  );
}
