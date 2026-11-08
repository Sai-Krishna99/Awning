(function () {
  const root = document.getElementById("order-root");
  const orderId = root.dataset.orderId;
  const initial = root.dataset.status;
  setInterval(async () => {
    try {
      const response = await fetch("/api/orders/" + orderId);
      if (!response.ok) return;
      const body = await response.json();
      if (body.status !== initial) window.location.replace("/o/" + orderId);
    } catch (err) {
      /* keep polling */
    }
  }, 3000);
})();
