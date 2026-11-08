(function () {
  const log = document.getElementById("chat-log");
  const intro = document.getElementById("chat-intro");
  const form = document.getElementById("composer");
  const input = document.getElementById("message");
  const sendButton = document.getElementById("send");
  const newChatButton = document.getElementById("new-chat");
  const PAID_PROMPT = "I've paid. Is my order confirmed?";
  let sessionId = null;
  let busy = false;
  let typing = null;
  let pendingChip = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function friendlyError(message) {
    const error = new Error(message);
    error.friendly = true;
    return error;
  }

  function append(node) {
    log.insertBefore(node, typing);
    (typing || node).scrollIntoView({ block: "end" });
    return node;
  }

  function addBubble(kind, text) {
    const row = el("div", "msg msg-" + kind);
    const bubble = row.appendChild(el("p", "bubble bubble-" + kind, text));
    if (kind === "error") bubble.setAttribute("role", "alert");
    return append(row);
  }

  function setBusy(value) {
    busy = value;
    sendButton.disabled = value;
    newChatButton.disabled = value;
    log.querySelectorAll("[data-prompt]").forEach(function (button) { button.disabled = value; });
    if (value) {
      typing = el("div", "msg msg-agent");
      typing.appendChild(el("p", "bubble bubble-agent text-ink-soft pulse", "Working on it…"));
      log.appendChild(typing);
      typing.scrollIntoView({ block: "end" });
    } else if (typing) {
      typing.remove();
      typing = null;
    }
  }

  function pretty(value) { return JSON.stringify(value, null, 2); }

  function plural(count, word) { return count + " " + word + (count === 1 ? "" : "s"); }

  function callSummary(name, args) {
    if (name === "find_shops") return "“" + (args.query || "") + "”";
    if (name === "get_menu") return args.shop_slug || "";
    if (name === "create_order") {
      const count = (args.items || []).reduce(function (sum, line) { return sum + (Number(line.quantity) || 0); }, 0);
      return plural(count, "item") + (args.customer_name ? " for " + args.customer_name : "");
    }
    return args.order_id || "";
  }

  function resultSummary(name, result) {
    if (result.error) return "error: " + (result.error.code || "failed");
    if (name === "find_shops") return plural((result.shops || []).length, "shop") + " found";
    if (name === "get_menu") return plural((result.items || []).length, "item");
    if (name === "create_order") return "total $" + result.total + ", priced by Awning";
    return "status: " + result.status;
  }

  function addToolCall(name, args) {
    const chip = el("details", "tool-chip is-pending");
    const summary = chip.appendChild(el("summary"));
    const dot = summary.appendChild(el("span", "tool-dot pulse"));
    dot.setAttribute("aria-hidden", "true");
    summary.appendChild(el("span", "tool-name", name));
    const summaryText = summary.appendChild(el("span", "tool-summary", callSummary(name, args)));
    const body = chip.appendChild(el("div", "tool-body"));
    body.appendChild(el("p", "tool-label", "Agent sent"));
    body.appendChild(el("pre", "", pretty(args)));
    append(chip);
    pendingChip = { name: name, chip: chip, dot: dot, summaryText: summaryText, body: body };
  }

  function safeHttpsUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" ? url.href : null;
    } catch (err) {
      return null;
    }
  }

  function addPayCard(result) {
    const href = safeHttpsUrl(result.approval_url);
    if (!href) return;
    const card = el("div", "card pay-card");
    card.appendChild(el("p", "font-semibold", "Total $" + result.total));
    card.appendChild(el("p", "text-sm text-ink-soft", "Priced by Awning from the menu, not by the agent."));
    const link = card.appendChild(el("a", "btn btn-accent w-full mt-3", "Review & pay with PayPal"));
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    card.appendChild(el("p", "mt-2 text-sm text-ink-soft", "Opens PayPal in a new tab. Nothing is charged until you approve. Then come back here."));
    const paid = card.appendChild(el("button", "btn btn-ghost w-full mt-3", PAID_PROMPT));
    paid.type = "button";
    paid.dataset.prompt = PAID_PROMPT;
    append(card);
  }

  function addToolResult(name, result) {
    const target = pendingChip && pendingChip.name === name ? pendingChip : null;
    pendingChip = null;
    if (target) {
      target.chip.classList.remove("is-pending");
      target.dot.classList.remove("pulse");
      if (result.error) target.chip.classList.add("is-error");
      target.summaryText.textContent += " → " + resultSummary(name, result);
      target.body.appendChild(el("p", "tool-label", "Awning returned"));
      target.body.appendChild(el("pre", "", pretty(result)));
    }
    if (!result.error && result.approval_url && result.status === "awaiting_payment") addPayCard(result);
  }

  function handleEvent(event) {
    if (event.type === "text") addBubble("agent", event.text);
    else if (event.type === "tool_call") addToolCall(event.name, event.args || {});
    else if (event.type === "tool_result") addToolResult(event.name, event.result || {});
    else if (event.type === "error") addBubble("error", event.message);
  }

  async function readEvents(stream) {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const data = frame.split("\n")
          .filter(function (line) { return line.startsWith("data:"); })
          .map(function (line) { return line.slice(5).replace(/^ /, ""); })
          .join("\n");
        if (!data) continue;
        const event = JSON.parse(data);
        if (event.type === "done") finished = true;
        else handleEvent(event);
      }
    }
    return finished;
  }

  async function ensureSession() {
    if (sessionId) return;
    const response = await fetch("/api/agent/sessions", { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw friendlyError((body && body.error && body.error.message) || "Couldn't start a chat. Please refresh and try again.");
    }
    sessionId = (await response.json()).session_id;
  }

  async function send(text) {
    text = text.trim();
    if (!text || busy) return;
    intro.hidden = true;
    addBubble("user", text);
    input.value = "";
    setBusy(true);
    try {
      await ensureSession();
      const response = await fetch("/api/agent/sessions/" + encodeURIComponent(sessionId) + "/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text }),
      });
      if (!response.ok) {
        if (response.status === 404) sessionId = null;
        const body = await response.json().catch(function () { return null; });
        throw friendlyError((body && body.error && body.error.message) || "Something went wrong. Please try again.");
      }
      if (!(await readEvents(response.body))) throw friendlyError("The connection dropped. Please try again.");
    } catch (err) {
      addBubble("error", err.friendly ? err.message : "Couldn't reach Awning. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    send(input.value);
  });

  log.addEventListener("click", function (event) {
    const button = event.target.closest("[data-prompt]");
    if (button) send(button.dataset.prompt);
  });

  newChatButton.addEventListener("click", function () {
    if (busy) return;
    sessionId = null;
    pendingChip = null;
    Array.from(log.children).forEach(function (child) { if (child !== intro) child.remove(); });
    intro.hidden = false;
    input.value = "";
  });
})();
