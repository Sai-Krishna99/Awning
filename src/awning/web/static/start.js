(() => {
  const form = document.getElementById("upload-form");
  const input = document.getElementById("photo");
  const zone = document.getElementById("drop-zone");
  const loading = document.getElementById("loading");
  const step = document.getElementById("loading-step");
  const button = document.getElementById("submit-btn");
  const steps = ["Looking at your photo...", "Reading the items...", "Matching prices...", "Almost there..."];
  let timer = null;

  const showName = () => {
    document.getElementById("file-name").textContent = input.files.length ? input.files[0].name : "";
  };
  input.addEventListener("change", showName);

  ["dragenter", "dragover"].forEach((name) =>
    zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach((name) =>
    zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.remove("dragging"); }));
  zone.addEventListener("drop", (event) => {
    if (event.dataTransfer.files.length) { input.files = event.dataTransfer.files; showName(); }
  });

  form.addEventListener("submit", (event) => {
    if (!input.files.length) { event.preventDefault(); input.click(); return; }
    loading.classList.remove("hidden");
    button.disabled = true;
    let index = 0;
    timer = setInterval(() => { index = Math.min(index + 1, steps.length - 1); step.textContent = steps[index]; }, 7000);
  });

  window.addEventListener("pageshow", () => {
    loading.classList.add("hidden");
    button.disabled = false;
    clearInterval(timer);
  });
})();
