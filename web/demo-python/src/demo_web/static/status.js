(() => {
  const panel = document.querySelector("#registration-panel");
  if (!panel) return;

  const pollMessage = document.querySelector("[data-poll-message]");
  const refreshButton = document.querySelector("[data-poll-refresh]");
  const toggleButton = document.querySelector("[data-poll-toggle]");
  const intervalMs = Number(panel.dataset.pollIntervalMs);
  const deadline = Date.now() + Number(panel.dataset.pollTimeoutMs);
  const initialPolling = panel.dataset.polling === "true";
  let automatic = initialPolling;
  let paused = !initialPolling;
  let userPaused = false;
  let inFlight = false;
  let closed = false;
  let timer = null;
  let controller = null;

  const setMessage = (message) => {
    if (pollMessage) pollMessage.textContent = message;
  };

  const updateRegistration = (registration, observedAt) => {
    const status = document.querySelector("[data-registration-status]");
    const refundStatus = document.querySelector("[data-refund-status]");
    const expiresAt = document.querySelector("[data-expires-at]");
    const offerExpiresAt = document.querySelector("[data-offer-expires-at]");
    const expiresContainer = document.querySelector("[data-expires-container]");
    const offerExpiresContainer = document.querySelector("[data-offer-expires-container]");
    const refundNote = document.querySelector("[data-refund-note]");
    const offerNote = document.querySelector("[data-offer-note]");
    const lastObserved = document.querySelector("[data-last-observed]");
    const cancelForm = document.querySelector('[data-action-form="cancel"]');
    const acceptForm = document.querySelector('[data-action-form="accept-offer"]');
    if (status) status.textContent = registration.status;
    if (refundStatus) refundStatus.textContent = registration.refundStatus;
    if (refundNote) {
      refundNote.hidden = !(
        registration.status === "CONFIRMED" && registration.refundStatus === "PENDING"
      );
    }
    if (offerNote) offerNote.hidden = registration.status !== "OFFERED";
    if (cancelForm) {
      cancelForm.hidden = ["WAITLISTED", "OFFERED", "PENDING_PAYMENT", "CONFIRMED"].includes(
        registration.status,
      )
        ? false
        : true;
    }
    if (acceptForm) acceptForm.hidden = registration.status !== "OFFERED";
    if (expiresAt) {
      if (expiresContainer) expiresContainer.hidden = !registration.expiresAt;
      expiresAt.textContent = registration.expiresAt
        ? `${new Date(registration.expiresAt).toISOString().slice(0, 16).replace("T", " ")} UTC`
        : "No deadline";
    }
    if (offerExpiresAt) {
      if (offerExpiresContainer) {
        offerExpiresContainer.hidden = !registration.offerExpiresAt;
      }
      offerExpiresAt.textContent = registration.offerExpiresAt
        ? `${new Date(registration.offerExpiresAt).toISOString().slice(0, 16).replace("T", " ")} UTC`
        : "No active offer";
    }
    if (lastObserved) {
      lastObserved.textContent = `Last observed ${new Date(observedAt)
        .toISOString()
        .slice(0, 16)
        .replace("T", " ")} UTC`;
    }
  };

  const schedule = () => {
    if (closed || paused || !automatic || Date.now() >= deadline) {
      if (Date.now() >= deadline) {
        automatic = false;
        paused = true;
        setMessage("The automatic refresh window ended. Use Refresh now for another observation.");
      }
      return;
    }
    timer = window.setTimeout(() => refresh(false), intervalMs);
  };

  const refresh = async (manual) => {
    if (closed || inFlight || (!manual && paused)) return;
    if (!manual && Date.now() >= deadline) {
      schedule();
      return;
    }
    inFlight = true;
    if (refreshButton) refreshButton.disabled = true;
    controller = new AbortController();
    let scheduleAgain = false;
    try {
      const response = await fetch(panel.dataset.pollUrl + `?persona_version=${panel.dataset.personaVersion}`, {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
      });
      const result = await response.json();
      if (response.status === 409 && result.state === "persona_changed") {
        automatic = false;
        paused = true;
        setMessage("Persona changed in another page. Automatic refresh stopped.");
        return;
      }
      if (!response.ok || result.state !== "observed") {
        const detail = result.error
          ? ` ${result.error.service} ${result.error.code}; correlation ${result.error.correlationId}.`
          : "";
        setMessage(`Refresh unavailable. Showing the last observed registration.${detail}`);
        scheduleAgain = automatic && !paused;
        return;
      }

      updateRegistration(result.registration, result.observedAt);
      if (!result.polling) {
        automatic = false;
        paused = true;
        if (toggleButton) toggleButton.disabled = true;
        setMessage("The backend state is terminal for automatic updates. Use Refresh now to recheck.");
        return;
      }
      setMessage(`Registration observed at ${new Date(result.observedAt).toISOString()}.`);
      scheduleAgain = automatic && !paused;
    } catch (error) {
      if (error.name !== "AbortError") {
        setMessage("Refresh unavailable. Showing the last observed registration.");
        scheduleAgain = automatic && !paused && !manual;
      }
    } finally {
      inFlight = false;
      controller = null;
      if (refreshButton) refreshButton.disabled = false;
      if (scheduleAgain) schedule();
    }
  };

  if (refreshButton) {
    refreshButton.addEventListener("click", () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      refresh(true);
    });
  }

  if (toggleButton) {
    toggleButton.addEventListener("click", () => {
      if (!automatic) return;
      userPaused = !userPaused;
      paused = userPaused || document.hidden;
      toggleButton.setAttribute("aria-pressed", String(paused));
      toggleButton.textContent = userPaused ? "Resume updates" : "Pause updates";
      if (paused) {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        setMessage("Updates paused. Use Refresh now for an explicit observation.");
      } else {
        refresh(false);
      }
    });
  }

  document.addEventListener("visibilitychange", () => {
    if (!automatic) return;
    if (document.hidden && !paused) {
      paused = true;
      if (toggleButton) {
        toggleButton.setAttribute("aria-pressed", "true");
        toggleButton.textContent = "Resume updates";
      }
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      setMessage("Updates paused while this page is hidden.");
    } else if (!document.hidden && paused && !userPaused) {
      paused = false;
      if (toggleButton) {
        toggleButton.setAttribute("aria-pressed", "false");
        toggleButton.textContent = "Pause updates";
      }
      refresh(false);
    }
  });

  window.addEventListener("pagehide", () => {
    closed = true;
    if (timer !== null) window.clearTimeout(timer);
    if (controller) controller.abort();
  });

  if (automatic) schedule();
})();
