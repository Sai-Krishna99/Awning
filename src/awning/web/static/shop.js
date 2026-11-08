(function () {
  const config = JSON.parse(document.getElementById("shop-config").textContent);
  const form = document.getElementById("order-form");
  const errorBox = document.getElementById("form-error");
  const linesEl = document.getElementById("cart-lines");
  const cart = [];

  const money = (cents) => (cents / 100).toFixed(2);

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
    errorBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function clearError() {
    errorBox.classList.add("hidden");
  }

  function unitCents(line) {
    const item = config.items[line.item_id];
    return item.price_cents + line.option_ids.reduce((sum, id) => sum + item.options[id].delta_cents, 0);
  }

  function renderCart() {
    linesEl.replaceChildren();
    let total = 0;
    cart.forEach((line, index) => {
      const item = config.items[line.item_id];
      const lineTotal = unitCents(line) * line.quantity;
      total += lineTotal;
      const li = document.createElement("li");
      li.className = "py-3 flex items-start justify-between gap-3";
      const text = document.createElement("div");
      const title = document.createElement("p");
      title.className = "font-medium";
      title.textContent = line.quantity + " × " + item.name;
      text.appendChild(title);
      if (line.option_ids.length) {
        const opts = document.createElement("p");
        opts.className = "text-sm text-ink-soft";
        opts.textContent = line.option_ids.map((id) => item.options[id].name).join(", ");
        text.appendChild(opts);
      }
      const side = document.createElement("div");
      side.className = "text-right";
      const price = document.createElement("p");
      price.className = "price font-semibold";
      price.textContent = "$" + money(lineTotal);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "text-sm text-terracotta underline min-h-[44px]";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => {
        cart.splice(index, 1);
        renderCart();
      });
      side.append(price, remove);
      li.append(text, side);
      linesEl.appendChild(li);
    });
    document.getElementById("cart-empty").classList.toggle("hidden", cart.length > 0);
    document.getElementById("cart-total-row").classList.toggle("hidden", cart.length === 0);
    document.getElementById("cart-total").textContent = money(total);
  }

  document.querySelectorAll("[data-item]").forEach((card) => {
    const qtyEl = card.querySelector(".qty");
    let quantity = 1;
    card.querySelectorAll("[data-step]").forEach((button) => {
      button.addEventListener("click", () => {
        quantity = Math.min(20, Math.max(1, quantity + Number(button.dataset.step)));
        qtyEl.textContent = quantity;
      });
    });
    card.querySelector(".add-btn").addEventListener("click", () => {
      const option_ids = Array.from(card.querySelectorAll("input[type=checkbox]:checked")).map((box) => box.value);
      cart.push({ item_id: card.dataset.item, option_ids, quantity });
      card.querySelectorAll("input[type=checkbox]").forEach((box) => (box.checked = false));
      quantity = 1;
      qtyEl.textContent = quantity;
      clearError();
      renderCart();
      document.getElementById("checkout").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  function validate() {
    clearError();
    if (!cart.length) return "Add at least one item to your order.";
    const data = new FormData(form);
    if (!String(data.get("customer_name")).trim()) return "Please enter your name.";
    if (!String(data.get("pickup_time")).trim()) return "Please enter a pickup time.";
    return null;
  }

  async function createOrder() {
    const data = new FormData(form);
    const response = await fetch("/api/s/" + encodeURIComponent(config.slug) + "/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lines: cart,
        customer_name: data.get("customer_name"),
        pickup_time: data.get("pickup_time"),
        note: data.get("note") || "",
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      const message = (body.error && body.error.message) || "Something went wrong. Please try again.";
      showError(message);
      throw new Error(message);
    }
    return body;
  }

  function setupRedirectButton() {
    const button = document.getElementById("redirect-pay");
    button.addEventListener("click", async () => {
      const problem = validate();
      if (problem) return showError(problem);
      button.disabled = true;
      try {
        const order = await createOrder();
        window.location.href = order.approval_url;
      } catch (err) {
        button.disabled = false;
      }
    });
  }

  let currentOrderId = null;
  let buttonsRendered = false;

  function renderPayPalButtons() {
    if (buttonsRendered || !window.paypal) return;
    buttonsRendered = true;
    window.paypal
      .Buttons({
        style: { shape: "pill", color: "gold", label: "pay", layout: "vertical" },
        onClick: (data, actions) => {
          const problem = validate();
          if (problem) {
            showError(problem);
            return actions.reject();
          }
          return actions.resolve();
        },
        createOrder: async () => {
          const order = await createOrder();
          currentOrderId = order.order_id;
          return order.paypal_order_id;
        },
        onApprove: async () => {
          const response = await fetch("/api/orders/" + currentOrderId + "/capture", { method: "POST" });
          const body = await response.json();
          const suffix = response.ok && body.status !== "awaiting_payment" ? "" : "?returned=1";
          window.location.href = "/o/" + currentOrderId + suffix;
        },
        onCancel: () => showError("Payment was cancelled. Your order is still here when you are ready."),
        onError: () => showError("PayPal ran into a problem. Please try again."),
      })
      .render("#paypal-button");
  }

  window.awningSdkReady = renderPayPalButtons;
  if (config.mode === "redirect") setupRedirectButton();
  else {
    renderPayPalButtons();
    window.addEventListener("load", () => {
      if (!window.paypal) document.getElementById("sdk-fallback").classList.remove("hidden");
    });
  }
  renderCart();
})();
