(() => {
  const container = document.querySelector("#report-tables");
  if (!container) return;
  const intervalMs = Math.max(1000, Number(container.dataset.refreshMs) || 3000);
  // Last change per summary card; kept until that card changes again.
  const changes = new Map();

  const readSummary = () => {
    const values = new Map();
    container.querySelectorAll(".summary-card[data-summary]").forEach((card) => {
      const text = card.querySelector("dd")?.textContent.trim() ?? "";
      const value = Number.parseFloat(text);
      if (Number.isFinite(value)) values.set(card.dataset.summary, { value, percent: text.endsWith("%") });
    });
    return values;
  };

  const tone = (trend, delta) => {
    if (trend === "up-good") return delta > 0 ? "good" : "warn";
    if (trend === "down-good") return delta < 0 ? "good" : "warn";
    if (trend === "up-bad") return delta > 0 ? "bad" : "good";
    return "neutral";
  };

  const showChanges = (before) => {
    const after = readSummary();
    container.querySelectorAll(".summary-card[data-summary]").forEach((card) => {
      const key = card.dataset.summary;
      const previous = before.get(key);
      const current = after.get(key);
      const fresh = previous && current && previous.value !== current.value;
      if (fresh) {
        const delta = current.value - previous.value;
        changes.set(key, {
          delta,
          unit: current.percent ? " pp" : "",
          tone: tone(card.dataset.trend, delta),
        });
      }
      const change = changes.get(key);
      if (!change) return;
      const up = change.delta > 0;
      const amount = `${up ? "+" : "−"}${Math.abs(change.delta)}${change.unit}`;
      const marker = card.querySelector("[data-summary-delta]");
      marker.textContent = `${up ? "▲" : "▼"} ${amount}`;
      marker.setAttribute("aria-label", `${up ? "up" : "down"} ${amount} since the previous update`);
      card.classList.add("is-changed", `is-${change.tone}`);
      if (fresh) {
        const value = card.querySelector("dd");
        value.classList.add("is-pulsing");
        value.addEventListener("animationend", () => value.classList.remove("is-pulsing"), { once: true });
      }
    });
  };

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
      const before = readSummary();
      container.innerHTML = await response.text();
      showChanges(before);
    } catch (error) {
      // Keep the last tables on screen; the next tick retries.
    } finally {
      if (again) window.setTimeout(refresh, intervalMs);
    }
  };

  window.setTimeout(refresh, intervalMs);
})();
