(() => {
  const container = document.querySelector("#report-tables");
  if (!container) return;
  const intervalMs = Math.max(1000, Number(container.dataset.refreshMs) || 3000);

  const refresh = async () => {
    let again = true;
    try {
      const response = await fetch(container.dataset.refreshUrl, {
        headers: { Accept: "text/html" },
        credentials: "same-origin",
        cache: "no-store",
      });
      if (response.redirected) {
        again = false;
        return;
      }
      if (!response.ok) return;
      container.innerHTML = await response.text();
    } catch (error) {
      // Keep the last tables on screen; the next tick retries.
    } finally {
      if (again) window.setTimeout(refresh, intervalMs);
    }
  };

  window.setTimeout(refresh, intervalMs);
})();
