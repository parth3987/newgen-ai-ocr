import { useState } from "react";
import { Link, useParams } from "react-router";

import { describe, get, post } from "../api";
import { atLeast, useAuth } from "../auth";
import { ExtractionView } from "../components/ExtractionView";
import { Badge, bytes, ErrorBox, JobProgress, Loading, Notice, show, useAsync, when } from "../components/ui";
import type { DocumentManifest, DocumentSummary, Job, JobEvent, JobSubmitted, OcrRun } from "../types";

const ACTIVE = new Set(["queued", "running"]);

export function DocumentDetail() {
  const { id = "" } = useParams();
  const base = `/api/v1/documents/${id}`;
  const status = useAsync(() => get<DocumentSummary>(`${base}/status`), [id], {
    ms: 2500,
    keepPolling: (d) => d.latest_job !== null && ACTIVE.has(d.latest_job.status),
  });
  const doc = status.data;
  const ready = doc?.status === "ready";
  const finished = doc?.latest_job ? !ACTIVE.has(doc.latest_job.status) : true;

  return (
    <section>
      <p>
        <Link to="/documents">← Documents</Link>
      </p>
      <ErrorBox error={status.error} title="Could not load the document" />
      {!doc ? (
        status.loading ? <Loading /> : null
      ) : (
        <>
          <h1>{doc.filename ?? doc.id}</h1>
          <dl className="facts">
            <dt>Status</dt>
            <dd>
              <Badge value={doc.status} /> {doc.error_code ? <span className="bad-text">{doc.error_code}</span> : null}
            </dd>
            <dt>Document id</dt>
            <dd className="mono">{doc.id}</dd>
            <dt>Format</dt>
            <dd>
              {doc.format ?? "—"} · {bytes(doc.size_bytes)} · {doc.page_count ?? "?"} page(s)
            </dd>
            <dt>Submitted</dt>
            <dd>
              {doc.submitted_by} · {when(doc.created_at)}
            </dd>
          </dl>
          {doc.status === "rejected" ? (
            <Notice kind="warn">
              The file was rejected at ingestion ({doc.error_code}). Upload a valid file; this one cannot be retried.
            </Notice>
          ) : null}
          <JobPanel job={doc.latest_job} onChange={status.reload} />
          {ready && finished ? <Results id={id} onQueued={status.reload} /> : null}
          {ready && !finished ? <Notice>Results appear here when processing finishes.</Notice> : null}
        </>
      )}
    </section>
  );
}

function JobPanel({ job, onChange }: { job: Job | null; onChange: () => void }) {
  const { session } = useAuth();
  const [showEvents, setShowEvents] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const events = useAsync(
    () => (showEvents && job ? get<JobEvent[]>(`/api/v1/jobs/${job.id}/events`) : Promise.resolve([])),
    [showEvents, job?.id, job?.updated_at],
  );
  if (!job) return null;
  const mayControl = job.submitted_by === session?.user || atLeast(session?.role, "admin");

  async function act(action: "cancel" | "retry") {
    setError(null);
    try {
      await post(`/api/v1/jobs/${job!.id}/${action}`);
      onChange();
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div className="panel">
      <h2>Processing</h2>
      <p>
        <JobProgress job={job} /> <span className="muted small">job {job.kind} · attempt {job.attempts} of {job.max_attempts}</span>
      </p>
      <ol className="stages">
        {job.stages.map((s) => (
          <li
            key={s}
            className={job.completed_stages.includes(s) ? "done" : job.current_stage === s ? "current" : ""}
          >
            {s}
          </li>
        ))}
      </ol>
      {job.status === "failed" ? (
        <Notice kind="warn">
          Failed: {job.error_message ?? job.error_code}. {mayControl ? "Retry continues after the stages already done." : ""}
        </Notice>
      ) : null}
      {job.cancel_requested && job.status === "running" ? (
        <Notice>Cancellation requested: the job stops before its next stage.</Notice>
      ) : null}
      <ErrorBox error={error} />
      <div className="row">
        {mayControl && ACTIVE.has(job.status) && !job.cancel_requested ? (
          <button type="button" onClick={() => void act("cancel")}>
            Cancel
          </button>
        ) : null}
        {mayControl && (job.status === "failed" || job.status === "cancelled") ? (
          <button type="button" className="primary" onClick={() => void act("retry")}>
            Retry
          </button>
        ) : null}
        <button type="button" onClick={() => setShowEvents(!showEvents)}>
          {showEvents ? "Hide history" : "Show history"}
        </button>
      </div>
      {showEvents && events.data ? (
        <table className="list compact">
          <tbody>
            {events.data.map((e, i) => (
              <tr key={i}>
                <td>{when(e.ts)}</td>
                <td>{e.event.replace(/_/g, " ")}</td>
                <td>{e.stage ?? ""}</td>
                <td>attempt {e.attempt}</td>
                <td className="muted small">
                  {Object.entries(e.detail)
                    .map(([k, v]) => `${k}=${show(v)}`)
                    .join(" ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

function Results({ id, onQueued }: { id: string; onQueued: () => void }) {
  const base = `/api/v1/documents/${id}`;
  const manifest = useAsync(() => get<DocumentManifest>(base), [id]);
  const runs = useAsync(() => get<string[]>(`${base}/extraction`), [id]);
  const [tab, setTab] = useState<"result" | "pages">("result");
  const run = runs.data?.includes("auto") ? "auto" : runs.data?.[0];

  return (
    <>
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "result"} onClick={() => setTab("result")}>
          Extracted data
        </button>
        <button type="button" role="tab" aria-selected={tab === "pages"} onClick={() => setTab("pages")}>
          Pages and text
        </button>
      </div>
      {tab === "pages" ? (
        <PagesView id={id} manifest={manifest.data} />
      ) : runs.data && run ? (
        <ExtractionView id={id} run={run} />
      ) : runs.data ? (
        <Notice kind="warn">No extraction result yet.</Notice>
      ) : (
        <ErrorBox error={runs.error} />
      )}
      <Reprocess id={id} onQueued={onQueued} />
    </>
  );
}

function PagesView({ id, manifest }: { id: string; manifest: DocumentManifest | null }) {
  const [page, setPage] = useState(1);
  const runs = useAsync(() => get<string[]>(`/api/v1/documents/${id}/ocr`), [id]);
  const runName = runs.data?.includes("auto") ? "auto" : runs.data?.[0];
  const ocr = useAsync(
    () => (runName ? get<OcrRun>(`/api/v1/documents/${id}/ocr/${runName}`) : Promise.resolve(null)),
    [id, runName],
  );
  if (!manifest) return <Loading />;
  const info = manifest.pages.find((p) => p.page_number === page);
  const ocrPage = ocr.data?.pages.find((p) => p.page_number === page);
  return (
    <div className="split">
      <div>
        <div className="row">
          <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            ← Page
          </button>
          <span>
            Page {page} of {manifest.page_count}
          </span>
          <button type="button" disabled={page >= manifest.page_count} onClick={() => setPage(page + 1)}>
            Page →
          </button>
        </div>
        {info?.quality?.flags.length ? (
          <p>
            {info.quality.flags.map((f) => (
              <Badge key={f} value={f} title="page quality flag" />
            ))}
          </p>
        ) : null}
        {info?.image ? (
          <img className="page" src={`/api/v1/documents/${id}/pages/${page}/image`} alt={`Page ${page}`} />
        ) : (
          <Notice>This page has embedded PDF text and no rendered image.</Notice>
        )}
      </div>
      <div>
        <h3>Extracted text {runName ? <span className="muted small">({runName} OCR run)</span> : null}</h3>
        <ErrorBox error={ocr.error} />
        {ocrPage ? (
          <>
            <p className="muted small">
              {ocrPage.engine ?? "—"} · <Badge value={ocrPage.status} /> · mean confidence{" "}
              {ocrPage.mean_confidence?.toFixed(2) ?? "—"}
              {ocrPage.review?.needs_review ? (
                <>
                  {" "}
                  · needs review: {ocrPage.review.reasons.join(", ")}
                </>
              ) : null}
            </p>
            <ol className="lines">
              {ocrPage.lines.map((line, i) => (
                <li key={i} className={line.confidence !== null && line.confidence < 0.7 ? "low" : ""}>
                  <span>{line.text}</span>
                  <span className="muted small"> {line.confidence?.toFixed(2) ?? ""}</span>
                </li>
              ))}
            </ol>
            {ocrPage.lines.length === 0 ? <p className="muted">No text was read on this page.</p> : null}
          </>
        ) : (
          <p className="muted">{runs.data && !runName ? "Not OCR'd yet." : "…"}</p>
        )}
        <p className="muted small">Lines read below 0.70 confidence are highlighted.</p>
      </div>
    </div>
  );
}

function Reprocess({ id, onQueued }: { id: string; onQueued: () => void }) {
  const schemas = useAsync(() => get<{ id: string; title: string }[]>("/api/v1/extraction/schemas"), []);
  const [type, setType] = useState("auto");
  const [message, setMessage] = useState<string | null>(null);

  async function run(path: string, body: unknown) {
    setMessage(null);
    try {
      const submitted = await post<JobSubmitted>(`/api/v1/documents/${id}/${path}`, body);
      setMessage(`Queued ${submitted.job.kind} job.`);
      onQueued();
    } catch (e) {
      setMessage(describe(e));
    }
  }

  return (
    <details className="panel">
      <summary>Process again</summary>
      <div className="row">
        <button type="button" onClick={() => void run("ocr", { compare: true })}>
          OCR, layout and extraction again
        </button>
        <label>
          Extract as
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="auto">detect type</option>
            {(schemas.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => void run("extraction", { document_type: type })}>
          Extraction again
        </button>
      </div>
      {message ? <p className="muted">{message}</p> : null}
    </details>
  );
}
