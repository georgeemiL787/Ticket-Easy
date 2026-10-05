// Ticket-Easy chat page. Plain JavaScript, no build step.
// Everything the customer or the agent wrote is put on the page with textContent, never as HTML.
(function () {
  "use strict";

  var params = new URLSearchParams(window.location.search);
  var tenantId = params.get("tenant_id") || "shop_001";
  var storageKey = "ticket-easy-conversation-" + tenantId;

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

  function loadConversationId() {
    try {
      var saved = window.localStorage.getItem(storageKey);
      if (saved) { return saved; }
      var fresh = newConversationId();
      window.localStorage.setItem(storageKey, fresh);
      return fresh;
    } catch (e) {
      return newConversationId(); // storage blocked: a conversation for this page view only
    }
  }

  var conversationId = loadConversationId();
  var base = "/v1/conversations/" + encodeURIComponent(conversationId);
  var tenantQuery = "tenant_id=" + encodeURIComponent(tenantId);

  function showStatus(text) {
    statusEl.textContent = text || "";
    statusEl.hidden = !text;
  }

  function addMessage(role, text, extra) {
    var item = document.createElement("li");
    item.className = "message " + role;

    var body = document.createElement("span");
    body.setAttribute("dir", "auto");
    body.textContent = text;
    if (role === "human_agent") {
      var who = document.createElement("span");
      who.className = "who";
      who.textContent = "Customer care";
      item.appendChild(who);
    }
    item.appendChild(body);

    if (extra && extra.citations && extra.citations.length) {
      var chips = document.createElement("div");
      chips.className = "chips";
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
      if (reply.awaiting) { parts.push("waiting for: " + reply.awaiting); }
      dev.appendChild(document.createTextNode(parts.join(" · ") + " · "));
      var link = document.createElement("a");
      link.href = "/v1/traces/" + encodeURIComponent(reply.trace_id) + "?" + tenantQuery;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "trace";
      dev.appendChild(link);
      item.appendChild(dev);
    }

    messagesEl.appendChild(item);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function errorText(body, status) {
    if (body && body.error && body.error.message) { return body.error.message; }
    return "Something went wrong (" + status + "). Please try again.";
  }

  function send(text) {
    sendButton.disabled = true;
    showStatus("");
    addMessage("customer", text);
    return fetch(base + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenant_id: tenantId, text: text, channel: "web" })
    }).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (body) {
        if (!response.ok) { showStatus(errorText(body, response.status)); return; }
        // A silent reply (a person already owns the chat) has no text to show.
        if (body.text) { addMessage("agent", body.text, { citations: body.citations, reply: body }); }
      });
    }).catch(function () {
      showStatus("Could not reach the server. Please try again.");
    }).then(function () {
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

  devToggle.addEventListener("change", function () {
    chatEl.classList.toggle("show-dev", devToggle.checked);
  });

  fetch("/v1/tenants/" + encodeURIComponent(tenantId) + "/welcome").then(function (response) {
    return response.ok ? response.json() : Promise.reject(response);
  }).then(function (welcome) {
    document.getElementById("title").textContent = welcome.display_name;
    document.title = welcome.display_name + " · chat";
    addMessage("agent", welcome.text);
  }).catch(function () {
    showStatus("This business is not available.");
  }).then(function () {
    showEarlierRepliesFromPeople();
    listenForHumanReplies();
    input.focus();
  });
})();
