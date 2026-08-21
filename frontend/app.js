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
function renderRouteTabs(scoredRoutes) {
  routeTabs.innerHTML = "";
  routeCompare.classList.remove("hidden");

  scoredRoutes.forEach((s) => {
    const tab = document.createElement("div");
    tab.className = "routeTab";
    tab.dataset.routeId = s.routeId;

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

      <button class="routeDetailsBtn" type="button">
        View safety details ▾
      </button>

      <div class="routeDetails hidden">

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

      const isHidden = details.classList.contains("hidden");

      details.classList.toggle("hidden", !isHidden);

      detailsBtn.textContent = isHidden
        ? "Hide safety details ▴"
        : "View safety details ▾";
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
                    "Accept-Language": "en"
                }
            }
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

  const status = document.getElementById("routeStatus");
  status.style.color =
    { Safe: "#16a34a", Moderate: "#f59e0b", Unsafe: "#dc2626" }[data.label] ||
    "#0f172a";

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

  async function getAreaName(lat, lon) {
    try {
        const response = await fetch(
            `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&zoom=18&addressdetails=1`,
            {
                headers: {
                    "Accept-Language": "en",
                },
            }
        );

        if (!response.ok) {
            throw new Error("Reverse geocoding failed");
        }

        const result = await response.json();
        const address = result.address || {};

        return (
            address.neighbourhood ||
            address.suburb ||
            address.quarter ||
            address.city_district ||
            address.town ||
            address.city ||
            "Area unavailable"
        );

    } catch (error) {
        console.error("Area lookup failed:", error);
        return "Area unavailable";
    }
}
  // ==========================================
// Segment-by-segment safety breakdown
// ==========================================

const segList = document.getElementById("segmentList");
segList.innerHTML = "";

for (let i = 0; i < data.segment_scores.length; i++) {

    const seg = data.segment_scores[i];

    const risk = Math.round(seg.risk_score * 100);
    const safety = 100 - risk;

    // Get area name from coordinates
    const areaName = await getAreaName(
        seg.point.lat,
        seg.point.lon
    );
    await new Promise(resolve => setTimeout(resolve, 1200));

    const row = document.createElement("div");
    row.className = "segmentRow";

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
}

/* ==========================================
   Nearby audits
========================================== */

async function loadNearbyAudits(lat, lon) {
  const el = document.getElementById("nearbyAuditsSummary");
  el.textContent = "Checking community reports...";
  try {
    const res = await getNearbyAudits(lat, lon, 1.5);
    if (res.count === 0) {
      el.textContent =
        "No community reports within 1.5km of the riskiest point yet. Be the first to add one below.";
    } else {
      const avg =
        res.audits.reduce((sum, a) => sum + a.rating, 0) / res.audits.length;
      el.innerHTML =
        `${res.count} report(s) within 1.5km — average felt-safety rating ${avg.toFixed(1)}/5.` +
        `<ul>${res.audits
          .slice(0, 3)
          .map(
            (a) =>
              `<li>${a.rating}/5${a.comment ? " — " + a.comment : ""} (${a.distance_km.toFixed(2)} km away)</li>`,
          )
          .join("")}</ul>`;
    }
  } catch (err) {
    el.textContent = "Could not load community reports right now.";
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
