// Review page (ADR-0028). No framework, no external assets. Document text is only ever
// inserted with textContent, never as HTML.
"use strict";

const API = "/api/v1";
const REASONS = [
  "ocr_misread", "wrong_value_selected", "missed_value", "ambiguity_resolved",
  "conflict_resolved", "normalisation_error", "other",
];
const REJECT_REASONS = [
  "illegible", "wrong_document_type", "unsupported_document", "incomplete_document",
  "duplicate", "other",
];
const state = { token: null, who: null, item: null, result: null, field: null };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function tag(text, kind = "") {
  return el("span", { class: `tag ${kind}` }, text);
}

async function call(path, options = {}) {
  const headers = { Authorization: `Bearer ${state.token}`, ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(API + path, { ...options, headers });
  if (response.status === 401) {
    signOut("Your token was not accepted.");
    throw new Error("unauthenticated");
  }
  if (!response.ok) {
    let message = response.statusText;
    try { message = (await response.json()).error.message; } catch (_) { /* not JSON */ }
    throw new Error(message);
  }
  const type = response.headers.get("content-type") || "";
  return type.startsWith("image/") ? response.blob() : response.json();
}

async function image(path, img) {
  try {
    const blob = await call(path);
    if (img.dataset.url) URL.revokeObjectURL(img.dataset.url);
    img.dataset.url = URL.createObjectURL(blob);
    img.src = img.dataset.url;
    img.hidden = false;
  } catch (_) {
    img.hidden = true;
  }
}

function itemPath() {
  return `/review/items/${state.item.document_id}/${state.item.layout_run}`;
}

function showError(message) {
  document.getElementById("item-error").textContent = message || "";
}

// ------------------------------------------------------------ sign in

function signOut(message) {
  state.token = null;
  try { sessionStorage.removeItem("ngocr-token"); } catch (_) { /* storage blocked */ }
  document.getElementById("app").hidden = true;
  document.getElementById("login").hidden = false;
  document.getElementById("logout").hidden = true;
  document.getElementById("who").textContent = "";
  document.getElementById("login-error").textContent = message || "";
}

async function signIn(token) {
  state.token = token;
  try {
    state.who = await call("/auth/whoami");
  } catch (e) {
    signOut("That token was not accepted.");
    return;
  }
  try { sessionStorage.setItem("ngocr-token", token); } catch (_) { /* storage blocked */ }
  document.getElementById("who").textContent = `${state.who.user} (${state.who.role})`;
  document.getElementById("login").hidden = true;
  document.getElementById("app").hidden = false;
  document.getElementById("logout").hidden = false;
  await loadQueue();
  // Deep link: /review#<document_id>/<layout_run>
  const link = /^#([0-9a-f]{32})\/([a-z][a-z0-9_]{0,31})$/.exec(location.hash);
  if (link) openItem(link[1], link[2]).catch((e) => showError(e.message));
}

// ------------------------------------------------------------ queue

async function loadQueue() {
  const filter = document.getElementById("status-filter").value;
  const query = filter === "open" ? "status=pending&status=in_review" : `status=${filter}`;
  const data = await call(`/review/items?${query}&limit=200`);
  document.getElementById("queue-count").textContent = `${data.total} item(s)`;
  const list = document.getElementById("queue-list");
  list.replaceChildren(...data.items.map((it) => el("li", {
    class: state.item && state.item.document_id === it.document_id ? "active" : "",
    onclick: () => openItem(it.document_id, it.layout_run),
  },
  el("div", {}, el("strong", {}, it.schema_id || "unclassified"), " ",
    tag(it.status), it.assigned_to ? tag(`@${it.assigned_to}`) : null),
  el("div", { class: "muted" }, `${it.document_id.slice(0, 8)}… · priority ${it.priority} · `
    + `${it.fields_to_review} to review`),
  el("div", {}, it.reasons.slice(0, 4).map((r) => tag(r, "warn"))))));
}

// ------------------------------------------------------------ item

async function openItem(documentId, layoutRun) {
  showError("");
  state.item = await call(`/review/items/${documentId}/${layoutRun}`);
  state.result = await call(`${itemPath()}/result`);
  history.replaceState(null, "", `#${documentId}/${layoutRun}`);
  renderItem();
  loadQueue();
}

async function reload() {
  await openItem(state.item.document_id, state.item.layout_run);
}

async function act(path, body) {
  showError("");
  try {
    await call(`${itemPath()}/${path}`, {
      method: "POST",
      body: JSON.stringify({ expected_version: state.item.version, ...body }),
    });
    await reload();
  } catch (e) {
    showError(e.message);
  }
}

function canReview() { return ["reviewer", "approver", "admin"].includes(state.who.role); }
function canApprove() { return ["approver", "admin"].includes(state.who.role); }
function isOpen() { return ["pending", "in_review"].includes(state.item.status); }
function mine() { return state.item.assigned_to === state.who.user; }

function renderItem() {
  const item = state.item;
  document.getElementById("item").hidden = false;
  document.getElementById("item-head").replaceChildren(...[
    el("h2", {}, `${item.schema_id || "Unclassified document"} `, tag(item.status)),
    el("div", { class: "muted" }, `Document ${item.document_id} · run ${item.layout_run} · `
      + `revision ${item.revision} · version ${item.version}`
      + (item.assigned_to ? ` · assigned to ${item.assigned_to}` : "")),
    el("div", { class: "muted" }, item.assessment.calibration_id
      ? `Calibration ${item.assessment.calibration_id}`
      : "No calibration loaded: no value is verified automatically"),
    item.decision ? el("div", {}, `Decision: ${item.decision.action} by ${item.decision.actor}`
      + (item.decision.reason ? ` (${item.decision.reason})` : "")
      + (item.decision.comment ? `: ${item.decision.comment}` : "")) : null,
  ].filter(Boolean));
  renderActions();
  renderFields();
  renderTables();
  renderChecks();
  document.getElementById("page-number").value = 1;
  showPage(null);
  renderHistory();
}

function renderActions() {
  const box = document.getElementById("item-actions");
  const buttons = [];
  if (isOpen() && canReview()) {
    if (!mine()) buttons.push(el("button", { class: "primary", onclick: () => act("claim", {}) }, "Claim"));
    else buttons.push(el("button", { onclick: () => act("release", {}) }, "Release"));
    const reason = el("select", {}, REJECT_REASONS.map((r) => el("option", { value: r }, r)));
    const comment = el("input", { placeholder: "comment (needed for other)" });
    buttons.push(el("span", { class: "row" }, reason, comment, el("button", {
      onclick: () => act("reject", { reason: reason.value, comment: comment.value || null }),
    }, "Reject")));
  }
  if (isOpen() && canApprove()) {
    const failing = state.result.checks.filter((c) => c.status === "failed").map((c) => c.id);
    const comment = el("input", { placeholder: "approval comment" });
    const ack = failing.map((id) => el("label", {}, el("input", { type: "checkbox", value: id }), ` accept failing ${id}`));
    buttons.push(el("span", { class: "row" }, ack, comment, el("button", {
      class: "primary",
      disabled: state.result.unresolved.length > 0,
      title: state.result.unresolved.length ? `Undecided: ${state.result.unresolved.join(", ")}` : "",
      onclick: () => act("approve", {
        comment: comment.value || null,
        acknowledge_failed_checks: ack.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => i.value),
      }),
    }, "Approve")));
  }
  box.replaceChildren(...buttons);
  if (state.result.unresolved.length && isOpen()) {
    box.append(el("div", { class: "muted" }, `Still to decide: ${state.result.unresolved.join(", ")}`));
  }
}

function decisionCell(d) {
  if (!d) return el("td", {}, "");
  const parts = [d.verified ? tag("verified", "ok") : null, ...d.reasons.map((r) => tag(r, "warn"))];
  const info = [];
  if (d.confidence !== null) info.push(`confidence ${d.confidence}`);
  if (d.threshold !== null) info.push(`threshold ${d.threshold}`);
  if (d.engine && d.engine.status !== "unavailable") info.push(`${d.engine.engine}: ${d.engine.status}`);
  return el("td", {}, parts, el("div", { class: "muted" }, info.join(" · ")),
    d.details.length ? el("div", { class: "muted" }, d.details.join("; ")) : null);
}

function editor(location, body) {
  const input = el("input", { placeholder: "value as printed" });
  const reason = el("select", {}, REASONS.map((r) => el("option", { value: r }, r)));
  const comment = el("input", { placeholder: "comment" });
  const send = (change) => act("corrections", { changes: [{ ...body, ...change }] });
  return el("div", { class: "edit" }, input, reason, comment,
    el("button", { onclick: () => send({ action: "set", value: input.value, reason: reason.value, comment: comment.value || null }) }, "Set"),
    el("button", { onclick: () => send({ action: "clear", reason: reason.value === "ocr_misread" ? "value_not_present" : reason.value, comment: comment.value || null }) }, "Not on page"),
    el("button", { onclick: () => send({ action: "confirm" }) }, "Confirm"));
}

function renderFields() {
  const decisions = Object.fromEntries(state.item.assessment.fields.map((d) => [d.field, d]));
  const editable = isOpen() && canReview() && mine() && state.item.schema_id;
  const rows = Object.entries(state.result.fields).map(([name, f]) => {
    const d = decisions[name];
    const correction = state.item.corrections[`fields.${name}`];
    const img = el("img", { class: "crop", alt: `Source of ${name}`, hidden: true });
    image(`${itemPath()}/crop?field=${name}`, img);
    const value = f.value === null ? el("span", { class: "muted" }, `(${f.status})`) : el("strong", {}, f.value);
    return el("tr", {},
      el("td", {}, el("button", { onclick: () => showPage(name), title: "Show on page" }, name),
        d && d.required ? el("div", { class: "muted" }, "required") : null),
      el("td", {}, value, el("div", {}, tag(f.source, f.source === "verified" ? "ok" : "")),
        correction ? el("div", { class: "muted" }, `${correction.action} by ${correction.actor} (${correction.reason})`) : null,
        editable ? editor(`fields.${name}`, { field: name }) : null),
      el("td", {}, img),
      el("td", {}, d ? [...d.checks_passed.map((c) => tag(c, "ok")), ...d.checks_failed.map((c) => tag(c, "bad"))] : ""),
      decisionCell(d));
  });
  document.querySelector("#fields tbody").replaceChildren(...rows);
}

function renderTables() {
  const box = document.getElementById("tables");
  const editable = isOpen() && canReview() && mine();
  box.replaceChildren(...state.item.assessment.tables.map((t) => {
    const records = state.result.tables[t.table] || [];
    const columns = records.length ? Object.keys(records[0]) : [];
    const flagged = new Set(t.cells_flagged);
    const head = el("tr", {}, el("th", {}, "#"), columns.map((c) => el("th", {}, c)));
    const body = records.map((r, i) => el("tr", {}, el("td", {}, String(i)), columns.map((c) => {
      const cell = el("td", {}, r[c] === null ? el("span", { class: "muted" }, "—") : String(r[c]));
      if (flagged.has(`${i}.${c}`)) {
        cell.append(" ", tag("check", "warn"));
        const img = el("img", { class: "crop", alt: `Source of ${c}`, hidden: true });
        image(`${itemPath()}/crop?table=${t.table}&record=${i}&column=${c}`, img);
        cell.append(img);
        if (editable) cell.append(editor(`tables.${t.table}.${i}.${c}`, { table: t.table, record: i, column: c }));
      }
      return cell;
    })));
    const confirmed = state.item.corrections[`tables.${t.table}`];
    return el("div", {},
      el("h2", {}, `Table ${t.table} `, t.verified ? tag("verified", "ok") : t.reasons.map((r) => tag(r, "warn"))),
      el("div", { class: "scroll" }, el("table", {}, el("thead", {}, head), el("tbody", {}, body))),
      confirmed ? el("div", { class: "muted" }, `confirmed by ${confirmed.actor}`) : null,
      editable && t.status === "extracted" && !t.verified
        ? el("button", { onclick: () => act("corrections", { changes: [{ table: t.table, action: "confirm" }] }) }, "Confirm table rows")
        : null);
  }));
}

function renderChecks() {
  document.getElementById("checks").replaceChildren(...state.result.checks.map((c) => el("li", {},
    tag(c.status, c.status === "passed" ? "ok" : c.status === "failed" ? "bad" : ""), ` ${c.id}: ${c.message}`)));
}

function showPage(field) {
  state.field = field;
  const page = document.getElementById("page-number").value || 1;
  document.getElementById("page-field").textContent = field ? `highlighting ${field}` : "";
  const query = field ? `?field=${field}` : "";
  image(`${itemPath()}/pages/${page}${query}`, document.getElementById("page-image"));
}

async function renderHistory() {
  const events = await call(`${itemPath()}/history`);
  document.getElementById("history").replaceChildren(...events.map((e) => el("li", {},
    `${e.ts.replace("T", " ").slice(0, 19)} · ${e.actor} · ${e.kind}`
    + (e.location ? ` · ${e.location}: ${e.previous_value ?? "—"} → ${e.new_value ?? "—"} (${e.reason})` : "")
    + (e.reason && !e.location ? ` (${e.reason})` : "")
    + (e.comment ? ` · “${e.comment}”` : ""))));
}

// ------------------------------------------------------------ start

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    signIn(document.getElementById("token").value.trim());
  });
  document.getElementById("logout").addEventListener("click", () => signOut(""));
  document.getElementById("refresh").addEventListener("click", loadQueue);
  document.getElementById("status-filter").addEventListener("change", loadQueue);
  document.getElementById("page-number").addEventListener("change", () => showPage(state.field));
  let saved = null;
  try { saved = sessionStorage.getItem("ngocr-token"); } catch (_) { /* storage blocked */ }
  if (saved) signIn(saved);
});
