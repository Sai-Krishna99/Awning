(function () {
  const root = document.getElementById("order-root");
  const orderId = root.dataset.orderId;
  const initialStatus = root.dataset.status;
  const initialHeld = root.dataset.held === "true";
  const startedAt = Date.now();
  const FAST_MS = 3000;
  const SLOW_MS = 10000;
  const FAST_FOR_MS = 60 * 1000;
  const STOP_AFTER_MS = 15 * 60 * 1000;

  function showRefreshNote() {
    const note = document.getElementById("poll-note");
    note.textContent = "This page has stopped checking for updates. Refresh to check again.";
    note.classList.remove("hidden");
  }

  function schedule() {
    const elapsed = Date.now() - startedAt;
    if (elapsed >= STOP_AFTER_MS) return showRefreshNote();
    setTimeout(check, elapsed < FAST_FOR_MS ? FAST_MS : SLOW_MS);
  }

  async function check() {
    try {
      const response = await fetch("/api/orders/" + encodeURIComponent(orderId), { cache: "no-store" });
      if (response.ok) {
        const body = await response.json();
        if (body.status !== initialStatus || Boolean(body.payment_held) !== initialHeld) {
          window.location.replace("/o/" + encodeURIComponent(orderId));
          return;
        }
      }
    } catch (err) {
      /* try again on the next tick */
    }
    schedule();
  }

  schedule();
})();
