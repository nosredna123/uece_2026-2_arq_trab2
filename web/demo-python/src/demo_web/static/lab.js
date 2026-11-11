(() => {
  const root = document.querySelector("#scenario-lab");
  if (!root) return;

  const message = root.querySelector("[data-lab-message]");
  const toggleButton = root.querySelector("[data-lab-toggle]");
  const reloadButton = root.querySelector("[data-lab-reload]");
  const POLL_INTERVAL_MS = Number(root.dataset.pollIntervalMs) || 1000;
  const MAX_POLL_ATTEMPTS = Number(root.dataset.maxPollAttempts) || 70;
  const GROUPS = {
    not_sent: "Not sent",
    in_flight: "In flight",
    success: "Service response",
    reconciled: "Resolved by read-back",
    unknown: "Unknown outcome",
    error: "Request error",
  };
  let pollAttempts = 0;
  let inFlight = false;
  let automatic = true;
  let manuallyPaused = false;
  let closed = false;
  let timer = null;
  let controller = null;

  const setMessage = (text) => {
    if (message) message.textContent = text;
  };

  const activeBoards = () =>
    [...document.querySelectorAll(".lab-run-board")].filter((board) =>
      ["PREPARING", "RUNNING"].includes(board.dataset.runState),
    );

  const setText = (element, text) => {
    if (element) element.textContent = String(text);
  };

  const appendText = (parent, tag, text, className) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = String(text);
    parent.append(element);
    return element;
  };

  const responseJson = async (response) => {
    let result;
    try {
      result = await response.json();
    } catch {
      throw new Error(`The server returned a non-JSON response (HTTP ${response.status}).`);
    }
    if (!response.ok) {
      const detail = typeof result.detail === "string" ? ` ${result.detail}` : "";
      throw new Error(`Request failed with HTTP ${response.status}.${detail}`);
    }
    return result;
  };

  const updateAvailability = (board, result) => {
    const host = board.querySelector("[data-capacity-content]");
    if (!host) return;
    host.replaceChildren();
    const readback = result.readback || {};
    const availability = readback.availability;
    if (!availability) {
      appendText(host, "p", "No final availability read-back is available.", "lab-observation-note");
      appendText(
        host,
        "p",
        `Read-back observed at ${readback.observedAt || "not observed"}.`,
        "lab-observation-note",
      );
      return;
    }

    const counters = document.createElement("dl");
    counters.className = "lab-counter-grid";
    counters.dataset.availability = "";
    [
      ["Capacity", availability.capacity, "capacity"],
      ["Occupied", availability.occupied, "occupied"],
      ["Available", availability.available, "available"],
      ["Registrations read", readback.registrationCount, "registration-count"],
    ].forEach(([label, value, key]) => {
      const wrapper = document.createElement("div");
      appendText(wrapper, "dt", label);
      const dd = appendText(wrapper, "dd", value);
      dd.dataset.counter = key;
      counters.append(wrapper);
    });
    host.append(counters);

    const expectedCapacity = Number(result.capacity);
    const occupied = Number(availability.occupied);
    const observedCapacity = Number(availability.capacity);
    const observedAvailable = Number(availability.available);
    const inconsistent =
      observedCapacity !== expectedCapacity ||
      observedAvailable !== observedCapacity - occupied;
    if (occupied > observedCapacity || inconsistent) {
      appendText(
        host,
        "p",
        occupied > observedCapacity
          ? `Anomaly: observed occupied count ${availability.occupied} exceeds capacity ${availability.capacity}. Raw backend values are preserved.`
          : `Anomaly: read-back counts are inconsistent (capacity ${availability.capacity}, occupied ${availability.occupied}, available ${availability.available}; preset capacity ${result.capacity}). Raw backend values are preserved.`,
        "lab-anomaly",
      ).setAttribute("role", "alert");
    }

    const seatGrid = document.createElement("ul");
    seatGrid.className = "lab-seat-grid";
    seatGrid.setAttribute("aria-label", "Seat occupancy illustration");
    const displayCapacity = Math.min(50, Math.max(0, expectedCapacity));
    for (let index = 0; index < displayCapacity; index += 1) {
      const occupiedSeat = index < occupied;
      const item = document.createElement("li");
      item.className = `lab-seat ${occupiedSeat ? "is-occupied" : "is-available"}`;
      appendText(item, "span", occupiedSeat ? "●" : "○").setAttribute("aria-hidden", "true");
      appendText(
        item,
        "span",
        `Seat ${index + 1} · ${occupiedSeat ? "observed occupied" : "not observed occupied"}`,
      );
      seatGrid.append(item);
    }
    host.append(seatGrid);
    appendText(
      host,
      "p",
      `Read-back observed at ${readback.observedAt || "not observed"}.`,
      "lab-observation-note",
    );
  };

  const updateParticipants = (board, observations) => {
    const groups = board.querySelector("[data-participant-groups]");
    if (!groups) return;
    Object.entries(GROUPS).forEach(([state, label]) => {
      const section = groups.querySelector(`[data-group="${state}"]`);
      if (!section) return;
      const list = section.querySelector("ul");
      if (!list) return;
      list.replaceChildren();
      const members = observations.filter((item) => item.state === state);
      setText(section.querySelector("[data-group-count]"), members.length);
      if (members.length === 0) {
        appendText(list, "li", "No participants in this group.", "lab-empty-group");
        return;
      }
      members.forEach((item) => {
        const row = document.createElement("li");
        appendText(row, "strong", item.participant || "Participant");
        if (item.status) appendText(row, "span", ` · ${item.status}`);
        if (item.errorCode) appendText(row, "code", ` · ${item.errorCode}`);
        if (item.latencyMs !== null && item.latencyMs !== undefined) {
          appendText(row, "span", ` · ${item.latencyMs} ms`);
        }
        if (item.registrationId) {
          const details = document.createElement("details");
          appendText(details, "summary", "Registration reference");
          appendText(details, "code", item.registrationId);
          row.append(details);
        }
        list.append(row);
      });
    });
  };

  const updateTimeline = (board, timeline) => {
    const list = board.querySelector("[data-timeline]");
    if (!list) return;
    list.replaceChildren();
    if (timeline.length === 0) {
      appendText(list, "li", "No request observations have been recorded.", "lab-empty-timeline");
      return;
    }
    timeline.forEach((item) => {
      const row = document.createElement("li");
      appendText(row, "time", item.at || "Time unavailable");
      appendText(row, "strong", item.participant || "Service observation");
      appendText(row, "span", `${item.state || "unknown"}${item.status ? ` · ${item.status}` : ""}`);
      if (item.errorCode) appendText(row, "code", item.errorCode);
      if (item.latencyMs !== null && item.latencyMs !== undefined) {
        appendText(row, "span", `${item.latencyMs} ms`);
      }
      list.append(row);
    });
  };

  const addActionButton = (host, action, runId, label) => {
    const button = document.createElement("button");
    button.className = "secondary-button";
    button.type = "button";
    button.dataset.runAction = action;
    button.dataset.runId = runId;
    button.textContent = label;
    host.append(button);
  };

  const updateActions = (board, result) => {
    const host = board.querySelector(".lab-run-actions");
    if (!host) return;
    const exportLink = host.querySelector("a[href$='/export']");
    host.replaceChildren();
    const runId = result.id;
    if (root.dataset.canRun === "true" && ["PREPARING", "RUNNING"].includes(result.state)) {
      addActionButton(host, "stop", runId, "Stop new requests and drain in-flight work");
    } else if (
      root.dataset.canRun === "true" &&
      result.state === "COMPLETED" &&
      ["last-seat", "registration-rush"].includes(result.preset) &&
      !result.fifo?.offeredRegistrationId &&
      result.readback?.statuses?.includes("CONFIRMED") &&
      result.readback?.statuses?.includes("WAITLISTED")
    ) {
      addActionButton(host, "cancel-confirmed", runId, "Open a seat and observe FIFO promotion");
    }
    if (
      root.dataset.canRun === "true" &&
      result.fifo?.offeredRegistrationId &&
      !result.fifo.acceptedRegistrationId
    ) {
      addActionButton(host, "accept-offer", runId, "Explicitly accept the observed offer");
    }
    if (exportLink) host.append(exportLink);
  };

  const updateBoard = (board, result) => {
    board.dataset.runState = result.state;
    setText(board.querySelector("[data-run-state-label]"), result.state);
    board.dataset.capacity = String(result.capacity);

    const verdict = board.querySelector("[data-verdict]");
    const verdictLabel = board.querySelector("[data-verdict-label]");
    if (verdict) {
      verdict.classList.remove("lab-verdict-expected", "lab-verdict-mismatch", "lab-verdict-inconclusive");
      verdict.classList.add(`lab-verdict-${String(result.verdict).toLowerCase()}`);
      verdict.dataset.verdict = result.verdict;
      setText(verdictLabel, result.verdict);
      const paragraph = verdict.querySelector("p");
      if (paragraph) {
        paragraph.textContent =
          result.verdict === "EXPECTED"
            ? "No unexpected capacity outcome was observed; review the counts and read-back below."
            : result.verdict === "MISMATCH"
              ? "A mismatch was observed. The backend values below are shown without clamping to the prediction."
              : "Evidence is inconclusive; unresolved requests or unavailable read-back are not treated as rollback or success.";
      }
      const previousErrors = verdict.querySelector(".lab-error-list");
      if (previousErrors) previousErrors.remove();
      if (Array.isArray(result.errors) && result.errors.length > 0) {
        const errorList = document.createElement("ul");
        errorList.className = "lab-error-list";
        result.errors.forEach((error) => appendText(errorList, "li", error));
        verdict.append(errorList);
      }
    }

    const workload = result.workload || {};
    setText(board.querySelector('[data-counter="dispatch-count"]'), workload.dispatchCount);
    setText(board.querySelector('[data-counter="completed-count"]'), workload.completedCount);
    setText(board.querySelector('[data-counter="peak-in-flight"]'), workload.peakInFlight);
    setText(board.querySelector('[data-counter="elapsed"]'), `${result.elapsedMs} ms`);
    setText(board.querySelector('[data-counter="throughput"]'), `${workload.throughputPerSecond} requests/s`);
    setText(board.querySelector('[data-counter="errors"]'), workload.errors);
    setText(board.querySelector('[data-counter="unknown"]'), workload.unknown);
    setText(
      board.querySelector('[data-counter="samples"]'),
      `${result.sampleRequests} / ${result.sampleBudget}`,
    );
    updateAvailability(board, result);
    updateParticipants(board, result.observations || []);
    updateTimeline(board, result.timeline || []);

    const fifo = result.fifo || {};
    setText(board.querySelector("[data-fifo-verdict]"), fifo.verdict || "NOT_RUN");
    setText(board.querySelector("[data-fifo-expected]"), fifo.expectedRegistrationId || "not observed");
    setText(board.querySelector("[data-fifo-offered]"), fifo.offeredRegistrationId || "not observed");
    setText(board.querySelector("[data-fifo-accepted]"), fifo.acceptedRegistrationId || "not observed");
    updateActions(board, result);
  };

  const schedulePoll = (delay = POLL_INTERVAL_MS) => {
    if (
      closed ||
      document.hidden ||
      !automatic ||
      manuallyPaused ||
      inFlight ||
      activeBoards().length === 0
    ) {
      return;
    }
    if (pollAttempts >= MAX_POLL_ATTEMPTS) {
      automatic = false;
      setMessage(
        `Automatic updates stopped after ${MAX_POLL_ATTEMPTS} requests. Reload observations to begin another bounded review.`,
      );
      return;
    }
    timer = window.setTimeout(pollActiveRuns, delay);
  };

  const pollActiveRuns = async () => {
    if (closed || inFlight || manuallyPaused || !automatic) return;
    const boards = activeBoards();
    if (boards.length === 0) return;
    inFlight = true;
    pollAttempts += 1;
    controller = new AbortController();
    try {
      for (const board of boards) {
        const response = await fetch(
          `/scenario-lab/runs/${encodeURIComponent(board.dataset.runId)}`,
          {
            method: "GET",
            headers: { Accept: "application/json" },
            credentials: "same-origin",
            cache: "no-store",
            signal: controller.signal,
          },
        );
        const result = await responseJson(response);
        updateBoard(board, result);
      }
      if (activeBoards().length > 0) {
        setMessage(`Refreshing retained observations (${pollAttempts} of ${MAX_POLL_ATTEMPTS}); no new registrations are sent.`);
        schedulePoll();
      } else {
        setMessage("Run finished. Final service observations are retained below; refresh does not restart it.");
      }
    } catch (error) {
      if (error.name !== "AbortError") {
        automatic = false;
        setMessage(`Automatic observation stopped: ${error.message} Last rendered observations remain available.`);
      }
    } finally {
      inFlight = false;
      controller = null;
    }
  };

  document.querySelectorAll("[data-run-preset]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (button.disabled || root.dataset.enabled !== "true" || root.dataset.canRun !== "true") return;
      button.disabled = true;
      setMessage("Preparing the selected fixed workload. No arbitrary targets can be selected.");
      try {
        const response = await fetch(
          `/scenario-lab/runs/${encodeURIComponent(button.dataset.runPreset)}`,
          {
            method: "POST",
            headers: {
              Accept: "application/json",
              "X-CSRF-Token": root.dataset.csrfToken,
            },
            credentials: "same-origin",
            cache: "no-store",
          },
        );
        await responseJson(response);
        window.location.assign("/scenario-lab");
      } catch (error) {
        setMessage(`The run was not started: ${error.message}`);
        button.disabled = false;
      }
    });
  });

  document.addEventListener("click", async (event) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest("[data-run-action]");
    if (!(button instanceof HTMLButtonElement)) return;
    if (button.disabled || root.dataset.canRun !== "true") return;
    const action = button.dataset.runAction;
    const rawRunId = button.dataset.runId;
    if (!action || !rawRunId) return;
    const runId = encodeURIComponent(rawRunId);
    button.disabled = true;
    setMessage(`${button.textContent.trim()} requested. Waiting for the service observation.`);
    try {
      const response = await fetch(`/scenario-lab/runs/${runId}/${action}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRF-Token": root.dataset.csrfToken,
        },
        credentials: "same-origin",
        cache: "no-store",
      });
      const result = await responseJson(response);
      const board = [...document.querySelectorAll(".lab-run-board")].find(
        (item) => item.dataset.runId === rawRunId,
      );
      if (board) updateBoard(board, result);
      setMessage("Control response received. Review the updated service observations below.");
      schedulePoll(0);
    } catch (error) {
      setMessage(`The control did not complete: ${error.message} Existing observations remain visible.`);
      button.disabled = false;
    }
  });

  if (toggleButton) {
    toggleButton.addEventListener("click", () => {
      if (!automatic) return;
      manuallyPaused = !manuallyPaused;
      toggleButton.setAttribute("aria-pressed", String(manuallyPaused));
      toggleButton.textContent = manuallyPaused ? "Resume updates" : "Pause updates";
      if (manuallyPaused) {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        setMessage("Observation updates paused. The run continues on the server.");
      } else {
        schedulePoll(0);
      }
    });
  }

  if (reloadButton) reloadButton.addEventListener("click", () => window.location.reload());

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
    } else if (!manuallyPaused) {
      schedulePoll(0);
    }
  });

  window.addEventListener("pagehide", () => {
    closed = true;
    if (timer !== null) window.clearTimeout(timer);
    if (controller) controller.abort();
  });

  if (activeBoards().length > 0) {
    setMessage(`Automatic observation is bounded to ${MAX_POLL_ATTEMPTS} requests and never starts a run.`);
    schedulePoll(0);
  }
})();
