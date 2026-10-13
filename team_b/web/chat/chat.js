// Ticket-Easy chat page. Plain JavaScript, no build step.
// Everything the customer or the agent wrote is put on the page with textContent, never as HTML.
(function () {
  "use strict";

  var params = new URLSearchParams(window.location.search);
  var tr = (window.TE && window.TE.tr) || function (text) { return text; };
  var tenantId = params.get("tenant_id") || "shop_001";

  var messagesEl = document.getElementById("messages");
  var statusEl = document.getElementById("status");
  var form = document.getElementById("composer");
  var input = document.getElementById("input");
  var sendButton = document.getElementById("send");
  var devToggle = document.getElementById("dev-toggle");
  var chatEl = document.getElementById("chat");

  function newConversationId() {
    var random = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
    return "web-" + random;
  }

  // Every load or refresh of the page starts a NEW conversation (nothing is kept between page views).
  var conversationId = newConversationId();
  var base = "/v1/conversations/" + encodeURIComponent(conversationId);
  var tenantQuery = "tenant_id=" + encodeURIComponent(tenantId);
  // staff trying a business that is not live yet bring a signed pass (?preview=...); customers never have one
  var previewToken = params.get("preview") || "";
  function passHeaders(extra) {
    var headers = extra || {};
    if (previewToken) { headers["X-Preview-Token"] = previewToken; }
    return headers;
  }

  function showStatus(text) {
    statusEl.textContent = text || "";
    statusEl.hidden = !text;
  }

  function addMessage(role, text, extra) {
    var item = document.createElement("li");
    item.className = "message " + role;

    var body = document.createElement("span");
    body.setAttribute("dir", "auto");
    // The sources are shown as chips in the developer view; the customer reads the answer without the source codes.
    var shown = text;
    if (extra && extra.citations) {
      extra.citations.forEach(function (citation) { shown = shown.split(" [" + citation + "]").join(""); });
    }
    body.textContent = shown;
    if (role === "human_agent") {
      var who = document.createElement("span");
      who.className = "who";
      who.textContent = tr("Customer care");
      item.appendChild(who);
    }
    item.appendChild(body);

    // Policy the customer did not ask about is for the human agent: developer view only, never in the customer's text.
    if (extra && extra.policy_quotes && extra.policy_quotes.length) {
      var quotes = document.createElement("div");
      quotes.className = "dev policy-quotes";
      extra.policy_quotes.forEach(function (quote) {
        var line = document.createElement("p");
        line.setAttribute("dir", "auto");
        line.textContent = quote;
        quotes.appendChild(line);
      });
      item.appendChild(quotes);
    }

    if (extra && extra.citations && extra.citations.length) {
      var chips = document.createElement("div");
      chips.className = "chips dev";
      extra.citations.forEach(function (citation) {
        var chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = citation;
        chips.appendChild(chip);
      });
      item.appendChild(chips);
    }

    if (extra && extra.reply) {
      var dev = document.createElement("div");
      dev.className = "dev";
      var reply = extra.reply;
      var parts = [reply.decision];
      if (reply.awaiting) { parts.push(tr("waiting for: ") + reply.awaiting); }
      dev.appendChild(document.createTextNode(parts.join(" · ") + " · "));
      var link = document.createElement("a");
      link.href = "/v1/traces/" + encodeURIComponent(reply.trace_id) + "?" + tenantQuery;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = tr("trace");
      dev.appendChild(link);
      item.appendChild(dev);
    }

    messagesEl.appendChild(item);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // While the agent works (it may read the policy and ask the AI model), show a bubble with moving dots and a short
  // line about what is happening, so the customer never stares at a frozen page.
  var PROGRESS = [
    [0, "Reading your message"],
    [2500, "Checking our policies and your order"],
    [7000, "Still working on it, almost there"],
    [20000, "This is taking longer than usual, thank you for waiting"]
  ];

  function showPending() {
    var item = document.createElement("li");
    item.className = "message agent pending";
    item.setAttribute("role", "status");
    var dots = document.createElement("span");
    dots.className = "dots";
    dots.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 3; i += 1) { dots.appendChild(document.createElement("i")); }
    var label = document.createElement("span");
    label.className = "pending-label";
    item.appendChild(dots);
    item.appendChild(label);
    messagesEl.appendChild(item);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    var timers = PROGRESS.map(function (step) {
      return window.setTimeout(function () { label.textContent = step[1]; }, step[0]);
    });
    return function stop() {
      timers.forEach(function (timer) { window.clearTimeout(timer); });
      if (item.parentNode) { item.parentNode.removeChild(item); }
    };
  }

  function errorText(body, status) {
    if (body && body.error && body.error.message) { return body.error.message; }
    return tr("Something went wrong ({status}). Please try again.", { status: status });
  }

  function send(text) {
    sendButton.disabled = true;
    showStatus("");
    addMessage("customer", text);
    var stopPending = showPending();
    return fetch(base + "/messages", {
      method: "POST",
      headers: passHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ tenant_id: tenantId, text: text, channel: "web" })
    }).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (body) {
        if (!response.ok) { showStatus(errorText(body, response.status)); return; }
        // A silent reply (a person already owns the chat) has no text to show.
        if (body.text) { addMessage("agent", body.text, { citations: body.citations, reply: body }); }
      });
    }).catch(function () {
      showStatus(tr("Could not reach the server. Please try again."));
    }).then(function () {
      stopPending();
      sendButton.disabled = false;
      input.focus();
    });
  }

  function listenForHumanReplies() {
    if (!window.EventSource) { return; }
    var source = new EventSource(base + "/events?" + tenantQuery);
    source.addEventListener("message", function (event) {
      try {
        var message = JSON.parse(event.data);
        addMessage(message.role === "human_agent" ? "human_agent" : "agent", message.text);
      } catch (e) { /* ignore a malformed event */ }
    });
  }

  function showEarlierRepliesFromPeople() {
    return fetch(base + "/outbox?" + tenantQuery).then(function (response) {
      return response.ok ? response.json() : [];
    }).then(function (items) {
      items.forEach(function (message) { addMessage("human_agent", message.text); });
    }).catch(function () { /* a new conversation has no outbox yet */ });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text) { return; }
    input.value = "";
    send(text);
  });

  var langSelect = document.getElementById("lang");
  if (langSelect && window.TE) {
    langSelect.value = window.TE.lang;
    langSelect.addEventListener("change", function () { window.TE.setLang(langSelect.value); });
  }

  devToggle.addEventListener("change", function () {
    chatEl.classList.toggle("show-dev", devToggle.checked);
  });

  fetch("/v1/tenants/" + encodeURIComponent(tenantId) + "/welcome", { headers: passHeaders() }).then(function (response) {
    return response.ok ? response.json() : Promise.reject(response);
  }).then(function (welcome) {
    document.getElementById("title").textContent = welcome.display_name;
    document.title = welcome.display_name + " · chat";
    addMessage("agent", welcome.text);
  }).catch(function () {
    showStatus(tr("This business is not available."));
  }).then(function () {
    showEarlierRepliesFromPeople();
    listenForHumanReplies();
    input.focus();
  });
})();
