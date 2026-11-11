(() => {
  const cards = document.querySelectorAll(".demo-activity-card[data-starts-at][data-ends-at]");

  const countdownText = (startsAt, endsAt, now) => {
    const remaining = (target) => Math.max(0, Math.floor((target - now) / 1000));
    const format = (seconds) => {
      const minutes = Math.floor(seconds / 60);
      return `in ${minutes}m ${seconds % 60}s`;
    };

    if (now < startsAt) {
      return `starts ${format(remaining(startsAt))}`;
    }
    if (now < endsAt) {
      return `scheduled window; ends ${format(remaining(endsAt))}`;
    }
    return "scheduled window has closed; refresh to inspect current service state";
  };

  const update = () => {
    const now = Date.now();
    cards.forEach((card) => {
      const target = card.querySelector("[data-countdown]");
      const startsAt = Date.parse(card.dataset.startsAt);
      const endsAt = Date.parse(card.dataset.endsAt);
      if (target && Number.isFinite(startsAt) && Number.isFinite(endsAt)) {
        target.textContent = countdownText(startsAt, endsAt, now);
      }
    });
  };

  update();
  window.setInterval(update, 1000);
})();
