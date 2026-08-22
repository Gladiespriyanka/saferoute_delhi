/* ==========================================
   SafeRoute Delhi - Main App
========================================== */

const predictBtn = document.getElementById("predictBtn");
const loading = document.getElementById("loading");
const loadingText = document.getElementById("loadingText");
const resultCard = document.getElementById("resultCard");
const routeCompare = document.getElementById("routeCompare");
const routeTabs = document.getElementById("routeTabs");

// State for the routes currently on screen, so tab clicks / feedback
// submission can reuse already-fetched data instead of re-calling the API.
let currentScoredRoutes = [];
let currentSelectedRouteId = null;
let currentRating = 0;

/* ==========================================
   Context sliders
========================================== */

const SLIDERS = [
  ["lightingInput", "lightingVal"],
  ["crowdInput", "crowdVal"],
  ["cctvInput", "cctvVal"],
  ["streetlightInput", "streetlightVal"],
  ["footpathInput", "footpathVal"],
];

SLIDERS.forEach(([inputId, labelId]) => {
  const input = document.getElementById(inputId);
  const label = document.getElementById(labelId);
  input.addEventListener("input", () => {
    label.textContent = input.value + "%";
  });
});

document.getElementById("reportConditions").addEventListener("change", (e) => {
  document
    .getElementById("contextInputs")
    .classList.toggle("hidden", !e.target.checked);
});

function collectUserContext() {
  const enabled = document.getElementById("reportConditions").checked;
  if (!enabled) return null;

  return {
    lighting_score:
      Number(document.getElementById("lightingInput").value) / 100,
    crowd_density: Number(document.getElementById("crowdInput").value) / 100,
    cctv_coverage: Number(document.getElementById("cctvInput").value) / 100,
    streetlight_density:
      Number(document.getElementById("streetlightInput").value) / 100,
    footpath_quality:
      Number(document.getElementById("footpathInput").value) / 100,
  };
}

/* ==========================================
   Travel time -> timestamp for /predict

   Uses today's date with the chosen clock time so time-of-day risk (a
   real feature the model + backend use) reflects what the user picked,
   not just "right now".
========================================== */

function buildTimestampFromTimeInput() {
  const timeVal = document.getElementById("time").value; // "HH:MM"
  if (!timeVal) return null;

  const [h, m] = timeVal.split(":").map(Number);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.toISOString();
}

/* ==========================================
   Main search flow
========================================== */

predictBtn.addEventListener("click", async function (e) {
  e.preventDefault();

  const origin = document.getElementById("origin").value.trim();
  const destination = document.getElementById("destination").value.trim();

  if (!origin || !destination) {
    alert("Please enter both source and destination.");
    return;
  }

  loading.style.display = "block";
  resultCard.classList.add("hidden");
  routeCompare.classList.add("hidden");
  predictBtn.disabled = true;

  try {
    loadingText.textContent = "Locating source & destination...";
    const source = await geocode(origin);
    const dest = await geocode(destination);

    setSourceMarker(source.lat, source.lon);
    setDestinationMarker(dest.lat, dest.lon);

    loadingText.textContent = "Finding alternative routes...";
    const routes = await getRoutes(source, dest);

    loadingText.textContent = `Scoring ${routes.length} route${routes.length > 1 ? "s" : ""} (live weather + traffic + model)...`;
    const timestamp = buildTimestampFromTimeInput();
    const userContext = collectUserContext();
    const scored = await analyzeRoutes(routes, timestamp, userContext);

    currentScoredRoutes = scored;
    renderRouteTabs(scored);
    window.selectRoute(scored[0].routeId);
  } catch (err) {
    console.error(err);
    alert(err.message || JSON.stringify(err, null, 2));
  } finally {
    loading.style.display = "none";
    predictBtn.disabled = false;
  }
});

/* ==========================================
   Route comparison tabs
========================================== */
// Same palette map.js uses for the polylines, so a route's card border
// matches the line it draws on the map — one visual language instead of
// two uncoordinated color systems.
const ROUTE_ACCENT_COLORS = ["#d6336c", "#2563eb", "#f59e0b", "#8a1c46", "#0891b2"];

function renderRouteTabs(scoredRoutes) {
  routeTabs.innerHTML = "";
  routeCompare.classList.remove("hidden");

  scoredRoutes.forEach((s, rank) => {
    const tab = document.createElement("div");
    tab.className = "routeTab";
    tab.dataset.routeId = s.routeId;
    tab.style.setProperty(
      "--routeAccent",
      ROUTE_ACCENT_COLORS[rank % ROUTE_ACCENT_COLORS.length],
    );

    const km = (s.route.distance / 1000).toFixed(1);
    const mins = Math.round(s.route.duration / 60);
    const pct = Math.round(s.prediction.overall_risk_score * 100);

    // Get top 3 actual model contributions
    const topFeatures = (s.prediction.top_feature_contributions || [])
      .slice(0, 3)
      .map((c) => {
        const arrow = c.direction === "increases_risk" ? "🔺" : "🔻";

        return `
          <li>
            ${arrow} ${c.feature}
            <span>${c.direction.replace("_", " ")}</span>
          </li>
        `;
      })
      .join("");

    // Get actual human-readable reasons
    const reasons = Object.values(s.prediction.grouped_reasons || {})
      .flat()
      .slice(0, 4)
      .map((reason) => `<li>${reason}</li>`)
      .join("");

    tab.innerHTML = `
      <div class="routeTabTop">
        <span class="routeTabName">
          <span class="routeSwatch" aria-hidden="true"></span>
          <span class="routeRankBadge">${rank + 1}</span>
          ${s.recommended ? "⭐ " : ""}
          ${s.routeId.replace("_", " ")}
        </span>

        <span class="routeTabLabel label-${s.prediction.label}">
          ${s.prediction.label}
        </span>
      </div>

      <div class="routeTabMeta">
        ${km} km · ${mins} min · risk ${pct}%
      </div>

      <div class="safetyBar routeTabBar">
        <div
          class="safetyFill ${s.prediction.label.toLowerCase()}"
          style="width: ${100 - pct}%"
        ></div>
      </div>

      <button class="routeDetailsBtn" type="button">
        View safety details ▾
      </button>

      <div class="routeDetails">

        <div class="routeDetailSection">
          <strong>Why this risk?</strong>

          ${reasons ? `<ul>${reasons}</ul>` : "<p>No major flags reported.</p>"}
        </div>

        <div class="routeDetailSection">
          <strong>Top model factors</strong>

          ${
            topFeatures
              ? `<ul>${topFeatures}</ul>`
              : "<p>No feature contributions available.</p>"
          }
        </div>

        <div class="routeDetailSection">
          <strong>Model confidence</strong>

          ${Math.round((s.prediction.confidence || 0) * 100)}%
        </div>

      </div>
    `;

    // Clicking the normal card selects the route
    tab.addEventListener("click", (e) => {
      if (e.target.closest(".routeDetailsBtn")) return;
      window.selectRoute(s.routeId);
    });

    // Expand / collapse details
    const detailsBtn = tab.querySelector(".routeDetailsBtn");
    const details = tab.querySelector(".routeDetails");

    detailsBtn.addEventListener("click", (e) => {
      e.stopPropagation();

      const isShown = details.classList.contains("show");

      details.classList.toggle("show", !isShown);

      detailsBtn.textContent = isShown
        ? "View safety details ▾"
        : "Hide safety details ▴";
    });

    routeTabs.appendChild(tab);
  });
}
// Select and display a route
window.selectRoute = function (routeId) {
  currentSelectedRouteId = routeId;

  // Highlight selected route card
  document.querySelectorAll(".routeTab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.routeId === routeId);
  });

  // Draw all routes and highlight the selected one
  drawScoredRoutes(currentScoredRoutes, routeId);

  // Find selected route
  const scored = currentScoredRoutes.find((s) => s.routeId === routeId);

  // Show all the existing safety information
  if (scored) {
    showPrediction(scored.prediction);
  }
};

// Reverse-geocode with a small cache so re-scoring the same route (or the
// same segment across route alternatives) doesn't re-hit Nominatim.
const areaCache = new Map();

async function getAreaName(lat, lon) {
  const key = `${lat.toFixed(4)},${lon.toFixed(4)}`;

  if (areaCache.has(key)) {
    return areaCache.get(key);
  }

  try {
    const response = await fetch(
      `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&zoom=18&addressdetails=1`,
      {
        headers: {
          "Accept-Language": "en",
        },
      },
    );

    if (!response.ok) {
      throw new Error("Reverse geocoding failed");
    }

    const result = await response.json();
    const address = result.address || {};

    const areaName =
      address.neighbourhood ||
      address.suburb ||
      address.quarter ||
      address.city_district ||
      address.town ||
      address.city ||
      "Area unavailable";

    areaCache.set(key, areaName);

    return areaName;
  } catch (error) {
    console.error("Area lookup failed:", error);
    return "Area unavailable";
  }
}

/* ==========================================
   Display full prediction detail
========================================== */

async function showPrediction(data) {
  resultCard.classList.remove("hidden");

  const risk = Math.round(data.overall_risk_score * 100);
  const safety = 100 - risk;

  document.getElementById("overallScore").innerHTML = safety + "%";

  document.getElementById("riskScore").innerHTML = `Risk: ${risk}%`;

  document.getElementById("routeStatus").innerHTML = data.label;

  document.getElementById("confidenceNote").innerHTML =
    `Model confidence: ${Math.round(data.confidence * 100)}%`;

  // Status color now comes from the shared design-token classes
  // (label-Safe / label-Moderate / label-Unsafe) instead of one-off
  // hex values, so it matches the route tabs and segment badges.
  const status = document.getElementById("routeStatus");
  status.classList.remove("status-Safe", "status-Moderate", "status-Unsafe");
  status.classList.add(`status-${data.label}`);

  // ----- Quick stat cards, read straight off the worst segment's feature
  // row so they reflect the actual scored inputs rather than being
  // reverse-engineered from SHAP contributions. -----
  const worst = data.worst_segment;
  document.getElementById("crimeScore").innerHTML = data.grouped_reasons.history
    .length
    ? "Flagged"
    : "Low";
  document.getElementById("lightingScore").innerHTML =
    data.top_feature_contributions.some((c) =>
      c.feature.toLowerCase().includes("light"),
    )
      ? "Contributing"
      : "OK";
  document.getElementById("crowdScore").innerHTML = worst ? worst.label : "--";

  // ----- Live data status -----
  const liveList = document.getElementById("liveDataList");
  liveList.innerHTML = "";

  const uniqueLiveData = new Map();

  (data.context_adjustments || []).forEach((adj) => {
    const source = String(adj.source || "Unknown")
      .trim()
      .toLowerCase();

    // Keep only the first entry for each source
    if (!uniqueLiveData.has(source)) {
      uniqueLiveData.set(source, adj);
    }
  });

  uniqueLiveData.forEach((adj) => {
    const li = document.createElement("li");

    const status = adj.data_available ? "🟢 Live" : "⚪ Unavailable";

    const adjustment = Math.round(Math.abs(adj.adjustment || 0) * 100);

    li.innerHTML = `
    <strong>${adj.source}</strong>
    — ${status}
    <span>${adj.description || "No additional information."}</span>
    ${adjustment > 0 ? `<em> (${adjustment}% impact)</em>` : ""}
  `;

    liveList.appendChild(li);
  });

  if (uniqueLiveData.size === 0) {
    liveList.innerHTML =
      "<li>No live external data was used for this prediction.</li>";
  }

  // ----- Feature contributions (model explainability) -----
  const contribList = document.getElementById("contributionList");
  contribList.innerHTML = "";
  data.top_feature_contributions.forEach((c) => {
    const li = document.createElement("li");
    const arrow = c.direction === "increases_risk" ? "🔺" : "🔻";
    li.textContent = `${arrow} ${c.feature} (${c.direction.replace("_", " ")}, ${c.contribution.toFixed(3)})`;
    contribList.appendChild(li);
  });

  // ----- Grouped human-readable reasons -----
  const groupsEl = document.getElementById("reasonGroups");
  groupsEl.innerHTML = "";
  const groupTitles = {
    environment: "🌳 Environment",
    infrastructure: "🏗️ Infrastructure",
    history: "📊 History",
    time: "🕒 Time of day",
  };
  Object.entries(data.grouped_reasons).forEach(([key, items]) => {
    if (!items.length) return;
    const block = document.createElement("div");
    block.className = "reasonGroup";
    block.innerHTML = `<h4>${groupTitles[key] || key}</h4><ul>${items.map((r) => `<li>${r}</li>`).join("")}</ul>`;
    groupsEl.appendChild(block);
  });
  if (groupsEl.innerHTML === "") {
    groupsEl.innerHTML = "<p>No notable flags for this route.</p>";
  }

  // ==========================================
  // Segment-by-segment safety breakdown
  //
  // Uses the shared, cached getAreaName() above instead of a local
  // re-declaration — that duplicate was silently shadowing the cache,
  // so every segment (even repeats) re-hit Nominatim and re-waited out
  // the throttle delay, which is what made this list feel like it was
  // stuck rendering "duplicate" repeated lookups.
  // ==========================================

  const segList = document.getElementById("segmentList");
  segList.innerHTML = "";

  for (let i = 0; i < data.segment_scores.length; i++) {
    const seg = data.segment_scores[i];

    const risk = Math.round(seg.risk_score * 100);
    const safety = 100 - risk;

    const key = `${seg.point.lat.toFixed(4)},${seg.point.lon.toFixed(4)}`;
    const alreadyCached = areaCache.has(key);

    const areaName = await getAreaName(seg.point.lat, seg.point.lon);

    // Only throttle when we actually hit the network — cached lookups
    // (repeated points, or re-rendering the same route) shouldn't pay
    // the delay again.
    if (!alreadyCached) {
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }

    const row = document.createElement("div");
    row.className = `segmentRow ${seg.label.toLowerCase()}`;

    row.innerHTML = `
        <div class="segmentTop">

            <div class="segmentInfo">

                <span class="segIndex">
                    Segment ${i + 1}
                </span>

                <span class="segArea">
                    📍 ${areaName}
                </span>

                <span class="segLabel label-${seg.label}">
                    ${seg.label}
                </span>

            </div>

            <div class="segmentSafety">
                <span>Safety</span>
                <strong>${safety}%</strong>
            </div>

        </div>

        <div class="safetyBar">
            <div
                class="safetyFill ${seg.label.toLowerCase()}"
                style="width: ${safety}%"
            ></div>
        </div>

        <div class="segmentBottom">
            <span>Risk level</span>
            <span>${risk}%</span>
        </div>
    `;

    segList.appendChild(row);
  }

  // ----- Nearby community audits for the riskiest point on this route -----
  loadNearbyAudits(worst.point.lat, worst.point.lon);

  // Reset feedback form for the newly selected route.
  currentRating = 0;
  updateStars(0);
  document.getElementById("feedbackComment").value = "";
  document.getElementById("feedbackStatus").textContent = "";
  const feedbackToggle = document.getElementById("feedbackToggle");
  const feedbackFormWrap = document.getElementById("feedbackFormWrap");
  if (feedbackToggle && feedbackFormWrap) {
    feedbackFormWrap.classList.add("hidden");
    feedbackToggle.classList.remove("open");
    feedbackToggle.setAttribute("aria-expanded", "false");
  }
}

/* ==========================================
   Nearby audits
========================================== */

function starGlyphs(rating) {
  const full = Math.round(rating);
  return "★".repeat(full) + "☆".repeat(5 - full);
}

async function loadNearbyAudits(lat, lon) {
  const el = document.getElementById("nearbyAuditsSummary");
  el.innerHTML = `<p class="auditEmpty">Checking community reports...</p>`;
  try {
    const res = await getNearbyAudits(lat, lon, 1.5);

    if (res.count === 0) {
      el.innerHTML =
        `<p class="auditEmpty">No community reports within 1.5km of the riskiest point yet. Be the first to add one below.</p>`;
      return;
    }

    const avg =
      res.audits.reduce((sum, a) => sum + a.rating, 0) / res.audits.length;
    const avgClass = avg >= 3.5 ? "" : avg >= 2.5 ? "mid" : "low";

    const cards = res.audits
      .slice(0, 3)
      .map(
        (a) => `
          <div class="auditCard">
            <div class="auditCardTop">
              <span class="auditStars">${starGlyphs(a.rating)}</span>
              <span class="auditDistance">${a.distance_km.toFixed(2)} km away</span>
            </div>
            ${a.comment ? `<p class="auditComment">${a.comment}</p>` : ""}
          </div>
        `,
      )
      .join("");

    el.innerHTML = `
      <div class="communitySummaryLine">
        <span class="avgBadge ${avgClass}">${avg.toFixed(1)}/5</span>
        <span>${res.count} report${res.count === 1 ? "" : "s"} within 1.5km</span>
      </div>
      <div class="auditList">${cards}</div>
    `;
  } catch (err) {
    el.innerHTML = `<p class="auditEmpty">Could not load community reports right now.</p>`;
  }
}

/* ==========================================
   Star rating + feedback submission
========================================== */

function updateStars(val) {
  document.querySelectorAll("#starRating span").forEach((star) => {
    star.classList.toggle("filled", Number(star.dataset.val) <= val);
  });
}

document.querySelectorAll("#starRating span").forEach((star) => {
  star.addEventListener("click", () => {
    currentRating = Number(star.dataset.val);
    updateStars(currentRating);
  });
});

document
  .getElementById("submitFeedbackBtn")
  .addEventListener("click", async () => {
    const statusEl = document.getElementById("feedbackStatus");

    if (!currentRating) {
      statusEl.textContent = "Pick a star rating first.";
      return;
    }
    const scored = currentScoredRoutes.find(
      (s) => s.routeId === currentSelectedRouteId,
    );
    if (!scored) return;

    const point = scored.prediction.worst_segment.point;
    const comment = document.getElementById("feedbackComment").value.trim();

    statusEl.textContent = "Submitting...";
    try {
      const result = await submitFeedback(
        point.lat,
        point.lon,
        currentRating,
        comment,
      );
      statusEl.textContent =
        `Thanks — saved (area ${result.area_code}). This area's audit-adjusted risk is now ` +
        `${Math.round(result.updated_area_audit_score * 100)}% and will factor into future predictions here.`;
      loadNearbyAudits(point.lat, point.lon);
    } catch (err) {
      statusEl.textContent = "Could not submit report: " + err.message;
    }
  });

/* ==========================================
   Backend Health Check
========================================== */

window.addEventListener("load", async () => {
  const ok = await checkBackend();
  if (!ok) {
    alert(
      "Backend is not reachable at:\n" +
        API.BASE_URL +
        "\n\nMake sure the API is running and reachable from this browser, e.g.:\n" +
        "  uvicorn app.main:app --host 0.0.0.0 --reload\n\n" +
        "If the backend runs on a different host/port, reload this page with " +
        "?api=http://<host>:<port> appended to the URL.",
    );
  }
});

/* ==========================================
   Start GPS Tracking
========================================== */

startTracking();

/* ==========================================
   CUSTOM CIRCULAR TIME PICKER
========================================== */

const timeInput = document.getElementById("time");
const openClock = document.getElementById("openClock");
const clockPicker = document.getElementById("clockPicker");
const clockOverlay = document.getElementById("clockOverlay");
const clockFace = document.getElementById("clockFace");

const selectedHourEl = document.getElementById("selectedHour");
const selectedMinuteEl = document.getElementById("selectedMinute");
const ampmButtons = document.querySelectorAll(".ampmBtn");

const clockDone = document.getElementById("clockDone");
const clockCancel = document.getElementById("clockCancel");

// State is kept in 12-hour form (1-12) + AM/PM. The dial itself only ever
// shows 12 numbers, so without an explicit AM/PM control there was no way
// to reach 13:00-23:59 or 00:00-00:59 by clicking — only whatever hour
// happened to be hardcoded as the default. The #time input and header
// still display/store the real 24-hour "HH:MM" the backend expects.
let selectedHour12 = 10;
let selectedPeriod = "PM";
let selectedM = 0;

let selectingMinutes = false;

function to24Hour(hour12, period) {
    let h = hour12 % 12;
    if (period === "PM") h += 12;
    return h;
}

function from24Hour(hour24) {
    const period = hour24 >= 12 ? "PM" : "AM";
    let hour12 = hour24 % 12;
    if (hour12 === 0) hour12 = 12;
    return { hour12, period };
}

function refreshClockHeader() {
    const hour24 = to24Hour(selectedHour12, selectedPeriod);

    selectedHourEl.textContent = String(hour24).padStart(2, "0");
    selectedMinuteEl.textContent = String(selectedM).padStart(2, "0");

    ampmButtons.forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.period === selectedPeriod);
    });
}

/* ------------------------------------------
   Create clock numbers
------------------------------------------ */

function createClockNumbers() {

    clockFace.innerHTML = "";

    const numbers = selectingMinutes
        ? [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]
        : [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];

    numbers.forEach((number, index) => {

        const button = document.createElement("button");

        button.type = "button";

        button.className = "clockNumber";

        button.textContent =
            selectingMinutes
                ? String(number).padStart(2, "0")
                : number;

        const isSelected = selectingMinutes
            ? number === selectedM
            : number === selectedHour12;

        button.classList.toggle("selected", isSelected);

        const angle = index * 30;

        const radius = 88;

        const center = 115;

        const x =
            center +
            radius * Math.sin(angle * Math.PI / 180);

        const y =
            center -
            radius * Math.cos(angle * Math.PI / 180);

        button.style.left = `${x}px`;
        button.style.top = `${y}px`;

        button.addEventListener("click", (event) => {

            // The document-level "click outside closes the picker" listener
            // checks whether the click target is still inside #clockPicker.
            // But selecting a number rebuilds the whole dial right here
            // (innerHTML wipe below), which detaches this very button from
            // the DOM mid-bubble — so without stopping propagation, that
            // outside-click check sees a detached node and closes the
            // picker on every single tap.
            event.stopPropagation();

            if (selectingMinutes) {

                selectedM = number;

                refreshClockHeader();
                createClockNumbers();

            } else {

                selectedHour12 = number;

                selectingMinutes = true;

                refreshClockHeader();
                createClockNumbers();
            }

        });

        clockFace.appendChild(button);
    });


    /* Center dot */

    const centerDot = document.createElement("div");

    centerDot.className = "clockCenter";

    clockFace.appendChild(centerDot);
}


/* ------------------------------------------
   AM / PM toggle — this is what actually
   unlocks the full 24-hour range, since the
   dial alone can only ever express 1-12.
------------------------------------------ */

ampmButtons.forEach((btn) => {
    btn.addEventListener("click", (event) => {
        event.stopPropagation();
        selectedPeriod = btn.dataset.period;
        refreshClockHeader();
    });
});


/* ------------------------------------------
   Open clock
------------------------------------------ */

openClock.addEventListener("click", () => {

    const current = timeInput.value || "22:00";

    const [h, m] = current.split(":").map(Number);

    const parsed = from24Hour(isNaN(h) ? 22 : h);
    selectedHour12 = parsed.hour12;
    selectedPeriod = parsed.period;
    selectedM = isNaN(m) ? 0 : m;

    selectingMinutes = false;

    refreshClockHeader();
    createClockNumbers();

    clockPicker.classList.remove("hidden");
    clockOverlay.classList.remove("hidden");
});


/* ------------------------------------------
   Click time field also opens clock
------------------------------------------ */

timeInput.addEventListener("click", () => {

    openClock.click();

});


/* ------------------------------------------
   Close on outside click (dial stays open
   for AM/PM + hour + minute picks, only
   dismissed by Done/Cancel/clicking away)
------------------------------------------ */

document.addEventListener("click", (e) => {
    if (clockPicker.classList.contains("hidden")) return;
    if (
        clockPicker.contains(e.target) ||
        openClock.contains(e.target) ||
        timeInput.contains(e.target)
    ) {
        return;
    }
    clockPicker.classList.add("hidden");
    clockOverlay.classList.add("hidden");
});


/* ------------------------------------------
   Also close when tapping the dimmed backdrop
------------------------------------------ */

clockOverlay.addEventListener("click", () => {
    clockPicker.classList.add("hidden");
    clockOverlay.classList.add("hidden");
});


/* ------------------------------------------
   Done
------------------------------------------ */

clockDone.addEventListener("click", () => {

    const hour24 = to24Hour(selectedHour12, selectedPeriod);

    timeInput.value =
        `${String(hour24).padStart(2, "0")}:${String(selectedM).padStart(2, "0")}`;

    clockPicker.classList.add("hidden");
    clockOverlay.classList.add("hidden");

});


/* ------------------------------------------
   Cancel
------------------------------------------ */

clockCancel.addEventListener("click", () => {

    clockPicker.classList.add("hidden");
    clockOverlay.classList.add("hidden");

});