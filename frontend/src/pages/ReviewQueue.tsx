import { Link, useSearchParams } from "react-router";

import { get, query } from "../api";
import { useAuth } from "../auth";
import { Badge, ErrorBox, Loading, useAsync, when } from "../components/ui";
import type { ReviewItemSummary } from "../types";

const VIEWS: Record<string, string[]> = {
  open: ["pending", "in_review"],
  mine: ["in_review"],
  pending: ["pending"],
  approved: ["approved", "auto_accepted"],
  rejected: ["rejected"],
};

export function ReviewQueue() {
  const { session } = useAuth();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") ?? "open";
  const reason = params.get("reason") ?? "";
  const queue = useAsync(
    () =>
      get<{ total: number; items: ReviewItemSummary[] }>(
        `/api/v1/review/items${query({
          status: VIEWS[view] ?? VIEWS.open,
          assigned_to: view === "mine" ? session?.user : undefined,
          reason: reason || undefined,
          limit: 200,
        })}`,
      ),
    [view, reason],
  );

  return (
    <section>
      <h1>Review queue</h1>
      <div className="filters">
        <div className="tabs" role="tablist">
          {Object.keys(VIEWS).map((v) => (
            <button key={v} type="button" role="tab" aria-selected={v === view} onClick={() => setParams({ view: v })}>
              {v === "mine" ? "assigned to me" : v}
            </button>
          ))}
        </div>
        <label>
          Reason
          <input
            placeholder="e.g. below_threshold"
            defaultValue={reason}
            onKeyDown={(e) => {
              if (e.key === "Enter") setParams({ view, reason: (e.target as HTMLInputElement).value });
            }}
          />
        </label>
      </div>
      <ErrorBox error={queue.error} title="Could not load the queue" />
      {queue.loading && !queue.data ? <Loading /> : null}
      {queue.data ? (
        <>
          <p className="muted">{queue.data.total} item(s), highest priority first</p>
          <table className="list">
            <thead>
              <tr>
                <th>Document</th>
                <th>Type</th>
                <th>Status</th>
                <th>Priority</th>
                <th>To check</th>
                <th>Why</th>
                <th>Queued</th>
              </tr>
            </thead>
            <tbody>
              {queue.data.items.map((it) => (
                <tr key={`${it.document_id}/${it.layout_run}`}>
                  <td>
                    <Link to={`/review/${it.document_id}/${it.layout_run}`}>{it.document_id.slice(0, 12)}</Link>
                  </td>
                  <td>{it.schema_id ?? "unclassified"}</td>
                  <td>
                    <Badge value={it.status} />
                    {it.assigned_to ? <div className="small muted">@{it.assigned_to}</div> : null}
                  </td>
                  <td>{it.priority}</td>
                  <td>{it.fields_to_review}</td>
                  <td className="small">
                    {it.reasons.slice(0, 4).map((r) => (
                      <Badge key={r} value={r} />
                    ))}
                  </td>
                  <td>{when(it.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </section>
  );
}
