"use strict";
/* The support inbox: a case list and a case page over /v1/handoff. Plain JavaScript, no build step.
   Every customer-supplied text is put in the page with textContent, never as HTML. */

const REFRESH_MS = 10000;
const REASONS = [
  "customer_request", "mandatory_risk", "policy_denied", "approval_required", "repeated_tool_failure",
  "unverified_result", "no_evidence", "dependency_unavailable", "low_confidence", "identity_failed",
  "ownership_mismatch", "high_frustration", "capability_missing", "unsupported",
];
const AI_SUGGESTION_LABEL = "AI suggestion, not approved: ";

/* ---- pure helpers (also used by the tests) ---- */

/** Which buttons make sense for this case and this person. Nothing is offered that the server would refuse. */
function availableActions(c, agent) {
  const me = (agent || "").trim();
  const claimedByMe = c.status === "claimed" && me !== "" && c.claimed_by === me;
  return {
    claim: c.status === "open" && me !== "",
    release: claimedByMe,
    reply: claimedByMe,
    resolve: claimedByMe,
    returnToAgent: claimedByMe,
    decide: claimedByMe && Boolean(c.pending_approval),
  };
}

/** "5 min", "2 h", "3 d" from an ISO time. */
function ageText(iso, nowMs) {
  const seconds = Math.max(0, Math.round(((nowMs ?? Date.now()) - Date.parse(iso)) / 1000));
  if (seconds < 90) return seconds + " s";
  if (seconds < 5400) return Math.round(seconds / 60) + " min";
  if (seconds < 129600) return Math.round(seconds / 3600) + " h";
  return Math.round(seconds / 86400) + " d";
}

/** The message to show for a failed API call: the server's own message when it sent one. */
function errorMessage(status, body) {
  const e = body && body.error;
  if (e && e.message) return e.message + (e.code ? " (" + e.code + ")" : "");
  return "Something went wrong (HTTP " + status + ").";
}

/** The summary to show and whether it was written by the AI model. */
function summaryToShow(pkg) {
  if (pkg.summary_source === "ai" && pkg.ai_summary) return { text: pkg.ai_summary, ai: true };
  return { text: pkg.summary, ai: false };
}

if (typeof module !== "undefined") {
  module.exports = { availableActions, ageText, errorMessage, summaryToShow, REASONS };
}

/* ---- the page ---- */

if (typeof document !== "undefined") {
  const $ = (id) => document.getElementById(id);
  const state = { selected: null, cursor: null, rows: [], timer: null, current: null };
  const params = new URLSearchParams(location.search);

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const child of children.flat()) {
      if (child !== null && child !== undefined && child !== false) {
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
      }
    }
    return node;
  }

  const agent = () => $("agent").value.trim();
  const tenant = () => $("tenant").value.trim() || "shop_001";

  function showError(message) {
    $("error").textContent = message || "";
    $("error").hidden = !message;
  }

  async function api(method, path, body) {
    let response;
    try {
      response = await fetch(path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (exc) {
      throw new Error("Cannot reach the server. Check your connection.");
    }
    let data = null;
    try { data = await response.json(); } catch (exc) { /* no body */ }
    if (!response.ok) throw new Error(errorMessage(response.status, data));
    return data;
  }

  /* ---- the list ---- */

  function listQuery() {
    const q = new URLSearchParams({ tenant_id: tenant(), limit: "50" });
    for (const [id, key] of [["f-status", "status"], ["f-priority", "priority"], ["f-reason", "reason"]]) {
      if ($(id).value) q.set(key, $(id).value);
    }
    if ($("f-mine").checked && agent()) q.set("claimed_by", agent());
    return q;
  }

  function renderRows() {
    const body = $("case-rows");
    body.replaceChildren(...state.rows.map((c) => {
      const row = el("tr", { class: "row" + (c.case_id === state.selected ? " selected" : ""), tabindex: "0",
        onclick: () => select(c.case_id), onkeydown: (e) => { if (e.key === "Enter") select(c.case_id); } },
        el("td", {}, el("span", { class: "badge " + c.priority, text: c.priority })),
        el("td", { text: c.reason.replaceAll("_", " ") }),
        el("td", { text: c.language || "" }),
        el("td", { text: ageText(c.created_at) }),
        el("td", { text: c.status.replaceAll("_", " ") + (c.claimed_by ? " · " + c.claimed_by : "") }),
        el("td", { dir: "auto", text: c.summary.length > 110 ? c.summary.slice(0, 110) + "…" : c.summary }));
      return row;
    }));
    $("empty").hidden = state.rows.length > 0;
    $("more").hidden = !state.cursor;
  }

  async function loadList(more) {
    try {
      const q = listQuery();
      if (more && state.cursor) q.set("cursor", state.cursor);
      const page = await api("GET", "/v1/handoff/cases?" + q);
      state.rows = more ? state.rows.concat(page.items) : page.items;
      state.cursor = page.next_cursor;
      renderRows();
      $("refreshed").textContent = "updated " + new Date().toLocaleTimeString();
      showError("");
    } catch (exc) {
      showError(exc.message);
    }
  }

  /* ---- one case ---- */

  function block(title, ...content) {
    return el("div", { class: "block" }, el("h3", { text: title }), ...content);
  }

  function facts(object) {
    const entries = Object.entries(object || {});
    if (!entries.length) return el("p", { class: "muted", text: "none" });
    return el("dl", { class: "facts" }, entries.flatMap(([k, v]) => [
      el("dt", { text: k }), el("dd", { dir: "auto", text: typeof v === "object" ? JSON.stringify(v) : String(v) })]));
  }

  function list(items, render, emptyText) {
    if (!items || !items.length) return el("p", { class: "muted", text: emptyText || "none" });
    return el("ul", { class: "plain" }, items.map((item) => el("li", {}, render(item))));
  }

  function renderCase(c) {
    state.current = c;
    const pkg = c.package;
    const summary = summaryToShow(pkg);
    $("case-pane").hidden = false;
    $("case-title").textContent = c.case_id + " · " + pkg.reason.replaceAll("_", " ");
    renderActions(c);
    const customer = pkg.customer;
    const body = [
      block("Summary",
        el("p", { dir: "auto", text: summary.text }, summary.ai ? " " : null),
        summary.ai ? el("span", { class: "tag", text: "AI-written summary" }) : null,
        el("p", {}, el("strong", { text: "Suggested next step: " }), el("span", { dir: "auto", text: pkg.suggested_next_step })),
        pkg.ai_suggestion ? el("p", { dir: "auto" }, el("span", { class: "tag", text: "AI" }), " ", pkg.ai_suggestion) : null,
        el("p", { class: "muted", text: "Why: " + (pkg.detail || pkg.reason) + " · priority " + pkg.priority })),
      c.pending_approval ? block("Waiting for approval", el("div", { class: "pending" },
        el("strong", { text: c.pending_approval.capability }), facts(c.pending_approval.arguments),
        el("p", { class: "muted", dir: "auto", text: c.pending_approval.reason }))) : null,
      block("Customer", facts({
        verified: customer.verified ? "yes (" + (customer.method || "checked") + ")" : "no",
        customer_id: customer.customer_id || "—", phone: customer.phone_masked || "—",
        orders: (customer.orders || []).join(", ") || "—", language: pkg.language || "—",
        intents: (pkg.intents || []).join(", ") || "—" })),
      block("Details the customer gave", facts(pkg.details)),
      block("Order facts", facts(pkg.order_facts)),
      pkg.safety_flags && pkg.safety_flags.length ? block("Safety flags", el("p", { text: pkg.safety_flags.join(", ") })) : null,
      block("Policy evidence", list(pkg.policy_quotes, (q) => el("blockquote", { dir: "auto" }, q.text, el("cite", { text: q.citation })),
        "no policy passage was used")),
      block("Rule checker answers", list(pkg.rule_answers, (r) =>
        r.action + ": " + r.decision + " (" + r.reason_code + ")" + (r.citations.length ? " · " + r.citations.join(", ") : ""))),
      block("Actions tried", list(pkg.attempted_actions, (a) => el("span", {},
        a.tool + " — " + a.state + (a.error ? " (" + a.error + ")" : ""),
        a.history.length ? el("div", { class: "muted", text: a.history.join("  |  ") }) : null,
        a.audit_id || a.execution_id ? el("div", { class: "muted", text: [a.audit_id && "audit " + a.audit_id, a.execution_id && "ref " + a.execution_id].filter(Boolean).join(" · ") }) : null))),
      block("Failures", list(pkg.failures, (f) => f.source + ": " + f.error_code + (f.message ? " — " + f.message : "") +
        (f.audit_id ? " · audit " + f.audit_id : ""))),
      pkg.similar_tickets && pkg.similar_tickets.length ? block("Similar past tickets",
        list(pkg.similar_tickets, (t) => t.category + ": " + t.resolution)) : null,
      block("Conversation", el("div", { class: "transcript" }, (pkg.transcript || []).map((l) =>
        el("div", { class: "line " + l.role, dir: "auto", text: l.text })))),
      block("History", list(c.events, (e) => new Date(e.at).toLocaleString() + " · " + e.actor + " · " + e.kind.replaceAll("_", " ") +
        (e.note ? " — " + e.note : ""))),
    ];
    $("case-body").replaceChildren(...body.filter(Boolean));
  }

  function renderActions(c) {
    const allowed = availableActions(c, agent());
    const buttons = [];
    const act = (label, path, extra, cls) => el("button", { type: "button", class: cls || "", text: label,
      onclick: () => doAction(path, extra ? extra() : {}) });
    if (allowed.claim) buttons.push(act("Claim", "claim", null, "primary"));
    if (allowed.release) buttons.push(act("Release", "release"));
    if (allowed.decide) {
      const note = el("input", { type: "text", maxlength: "1000", placeholder: "note (optional)", "aria-label": "Decision note" });
      buttons.push(note,
        el("button", { type: "button", class: "primary", text: "Approve", onclick: () => doAction("decision", { approve: true, note: note.value || null }) }),
        el("button", { type: "button", class: "danger", text: "Reject", onclick: () => doAction("decision", { approve: false, note: note.value || null }) }));
    }
    if (allowed.returnToAgent) buttons.push(act("Give back to assistant", "return-to-agent"));
    if (allowed.resolve) buttons.push(act("Resolve", "resolve", null, "primary"));
    $("actions").replaceChildren(...buttons);
    $("reply-form").hidden = !allowed.reply;
  }

  async function doAction(path, extra) {
    if (!agent()) { showError("Type your name first."); return; }
    try {
      const updated = await api("POST", "/v1/handoff/cases/" + encodeURIComponent(state.selected) + "/" + path,
        { agent: agent(), ...extra });
      showError("");
      renderCase(updated);
      await loadList(false);
    } catch (exc) {
      showError(exc.message);
      await openCase(state.selected); // the case may have changed under us: show what it is now
    }
  }

  async function openCase(id) {
    try {
      renderCase(await api("GET", "/v1/handoff/cases/" + encodeURIComponent(id)));
    } catch (exc) {
      showError(exc.message);
    }
  }

  function select(id) {
    state.selected = id;
    location.hash = id;
    renderRows();
    openCase(id);
  }

  /* ---- start ---- */

  function init() {
    $("agent").value = (() => { try { return localStorage.getItem("inbox.agent") || ""; } catch (e) { return ""; } })();
    if (params.get("tenant_id")) $("tenant").value = params.get("tenant_id");
    for (const reason of REASONS) $("f-reason").append(el("option", { value: reason, text: reason.replaceAll("_", " ") }));
    $("agent").addEventListener("input", () => {
      try { localStorage.setItem("inbox.agent", agent()); } catch (e) { /* private mode */ }
      if (state.current) renderActions(state.current);
    });
    $("tenant").addEventListener("change", () => loadList(false));
    $("filters").addEventListener("change", () => loadList(false));
    $("more").addEventListener("click", () => loadList(true));
    $("reply-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const text = $("reply-text").value.trim();
      if (!text) return;
      if (!agent()) { showError("Type your name first."); return; }
      try {
        const updated = await api("POST", "/v1/handoff/cases/" + encodeURIComponent(state.selected) + "/reply", { agent: agent(), text });
        $("reply-text").value = "";
        showError("");
        renderCase(updated);
      } catch (exc) {
        showError(exc.message);
      }
    });
    state.selected = location.hash.slice(1) || null;
    loadList(false);
    if (state.selected) openCase(state.selected);
    state.timer = setInterval(() => {
      loadList(false);
      if (state.selected && document.activeElement !== $("reply-text") && !$("actions").contains(document.activeElement)) openCase(state.selected);
    }, REFRESH_MS);
  }

  init();
}
