(function () {
  const root = document.getElementById("board");
  const token = root.dataset.token;
  const list = document.getElementById("orders");
  const empty = document.getElementById("empty");
  const live = document.getElementById("live");
  const errorBox = document.getElementById("board-error");
  const soundButton = document.getElementById("sound-toggle");
  const api = "/api/m/" + encodeURIComponent(token);
  const CHANNELS = { agent: "AI agent", web: "Web", demo_agent: "Demo agent" };
  const STATUS = {
    awaiting_payment: "Waiting for payment", paid: "Paid", ready: "Ready for pickup",
    picked_up: "Picked up", refunded: "Refunded",
  };
  const RANK = { paid: 0, ready: 1, awaiting_payment: 2, picked_up: 3, refunded: 3 };
  const orders = new Map();
  let audio = null;

  function money(cents) { return "$" + (cents / 100).toFixed(2); }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showError(message) {
    errorBox.textContent = message || "";
    errorBox.classList.toggle("hidden", !message);
  }

  function showSoundButton() {
    soundButton.classList.remove("hidden");
  }

  // Phones suspend audio when the board is backgrounded; resolves true once audio can play again.
  function resumeAudio() {
    return audio.resume()
      .then(function () { return audio.state === "running"; }, function () { return false; })
      .then(function (running) {
        if (!running) showSoundButton();
        return running;
      });
  }

  function chime() {
    if (!audio) return;
    if (audio.state === "running") return play();
    resumeAudio().then(function (running) { if (running) play(); });
  }

  function play() {
    const now = audio.currentTime;
    [660, 880].forEach(function (freq, i) {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + i * 0.18);
      gain.gain.exponentialRampToValueAtTime(0.35, now + i * 0.18 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.18 + 0.4);
      osc.connect(gain).connect(audio.destination);
      osc.start(now + i * 0.18);
      osc.stop(now + i * 0.18 + 0.45);
    });
  }

  soundButton.addEventListener("click", function () {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) {
      audio = audio || new Ctx();
      resumeAudio().then(function (running) { if (running) play(); });
    }
    if (navigator.vibrate) navigator.vibrate(30);
    soundButton.classList.add("hidden");
  });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && audio && audio.state !== "running") resumeAudio();
  });

  async function act(order, path, button) {
    showError("");
    button.disabled = true;
    try {
      const response = await fetch(api + "/orders/" + encodeURIComponent(order.id) + path.url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: path.body ? JSON.stringify(path.body) : undefined,
      });
      const body = await response.json().catch(function () { return {}; });
      if (!response.ok) {
        showError(body.message || "That didn't work. Please try again.");
        if (body.paypal_url) {
          const link = el("a", "underline font-semibold ml-1", "Open PayPal activity");
          link.href = body.paypal_url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          errorBox.appendChild(link);
        }
        return;
      }
      upsert(body, false);
    } catch (err) {
      showError("Couldn't reach the server. Please try again.");
    } finally {
      button.disabled = false;
    }
  }

  function actionButton(order, label, cls, path) {
    const button = el("button", "btn " + cls, label);
    button.type = "button";
    button.addEventListener("click", function () {
      if (path.confirm && !window.confirm(path.confirm)) return;
      act(order, path, button);
    });
    return button;
  }

  function card(order) {
    const awaiting = order.status === "awaiting_payment";
    const done = order.status === "picked_up" || order.status === "refunded";
    const node = el("article", "card order-card" + (awaiting ? " is-awaiting" : "") + (done ? " is-done" : ""));
    node.dataset.orderId = order.id;
    const head = el("div", "flex items-start justify-between gap-2");
    head.appendChild(el("h2", "text-xl font-bold text-green", order.customer_name));
    head.appendChild(el("span", "font-semibold", money(order.total_cents)));
    node.appendChild(head);
    const meta = el("p", "text-sm text-ink-soft mt-1");
    meta.appendChild(el("span", "", "Pickup " + order.pickup_time + " "));
    meta.appendChild(el("span", "badge" + (order.channel === "agent" ? " badge-ai" : ""), CHANNELS[order.channel] || order.channel));
    node.appendChild(meta);
    const items = el("ul", "mt-2 text-sm grid gap-1");
    order.lines.forEach(function (line) {
      const text = line.quantity + " × " + line.name + (line.option_names.length ? " (" + line.option_names.join(", ") + ")" : "");
      items.appendChild(el("li", "", text));
    });
    node.appendChild(items);
    if (order.note) node.appendChild(el("p", "mt-2 text-sm italic text-ink-soft", "Note: " + order.note));
    node.appendChild(el("p", "mt-2 text-sm font-semibold" + (awaiting ? " text-ink-soft" : " text-terracotta-deep"), STATUS[order.status] || order.status));
    const actions = el("div", "order-actions flex flex-wrap gap-2 mt-3");
    if (order.status === "paid") actions.appendChild(actionButton(order, "Ready", "btn-primary", { url: "/status", body: { status: "ready" } }));
    if (order.status === "ready") actions.appendChild(actionButton(order, "Picked up", "btn-primary", { url: "/status", body: { status: "picked_up" } }));
    if (["paid", "ready", "picked_up"].indexOf(order.status) !== -1) actions.appendChild(actionButton(order, "Refund", "btn-ghost", {
      url: "/refund", confirm: "Refund " + money(order.total_cents) + " to " + order.customer_name + "?",
    }));
    if (actions.children.length) node.appendChild(actions);
    return node;
  }

  function render(flashId) {
    const sorted = Array.from(orders.values()).sort(function (a, b) {
      return RANK[a.status] - RANK[b.status] || (a.created_at < b.created_at ? 1 : -1);
    });
    list.replaceChildren.apply(list, sorted.map(function (order) {
      const node = card(order);
      if (order.id === flashId) node.classList.add("flash");
      return node;
    }));
    empty.classList.toggle("hidden", sorted.length > 0);
  }

  function upsert(order, flash) {
    orders.set(order.id, order);
    render(flash ? order.id : null);
  }

  async function loadOrders() {
    try {
      const response = await fetch(api + "/orders", { cache: "no-store" });
      if (!response.ok) return;
      const body = await response.json();
      orders.clear();
      body.orders.forEach(function (order) { orders.set(order.id, order); });
      render(null);
    } catch (err) {
      /* the stream reconnect will retry */
    }
  }

  function onPaid(event) {
    upsert(JSON.parse(event.data), true);
    chime();
    if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
  }

  function connect() {
    const source = new EventSource(api + "/events");
    source.onopen = function () { live.textContent = "Live"; loadOrders(); };
    source.addEventListener("order_created", function (e) { upsert(JSON.parse(e.data), false); });
    source.addEventListener("order_updated", function (e) { upsert(JSON.parse(e.data), false); });
    source.addEventListener("order_paid", onPaid);
    source.onerror = function () {
      live.textContent = "Reconnecting…";
      source.close();
      setTimeout(connect, 3000);
    };
  }

  loadOrders();
  connect();
})();
