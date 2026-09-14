import { modeById } from "../lib/config.js";
import CommunityPanel from "./CommunityPanel.jsx";
import ScoreRing from "./ScoreRing.jsx";
import SegmentList from "./SegmentList.jsx";
import {
  confidenceWord,
  coverageWord,
  duration,
  km,
  labelTone,
  pct,
  verdictSentence,
} from "../lib/format.js";

const REASON_TITLES = {
  environment: "Around you",
  infrastructure: "Infrastructure",
  history: "History and reports",
  time: "Time of day",
};

/**
 * Everything behind the score for the selected route.
 *
 * Order is deliberate: the answer, then how sure we are, then the evidence
 * in decreasing order of how much it should change your mind.
 */
export default function RouteDetail({ entry, fastest }) {
  const { prediction } = entry;
  const tone = labelTone(prediction.label);
  const risk = pct(prediction.overall_risk_score);
  const safety = 100 - risk;
  const worst = prediction.worst_segment;

  const topDriver = (prediction.top_feature_contributions || []).find(
    (item) => item.direction === "increases_risk",
  );

  // One entry per source: a six-segment route returns a weather reading per
  // segment, and six near-identical rows help nobody.
  const liveBySource = new Map();
  for (const adjustment of prediction.context_adjustments || []) {
    const key = String(adjustment.source || "unknown").toLowerCase();
    if (!liveBySource.has(key)) liveBySource.set(key, adjustment);
  }

  // hour_sin and hour_cos both display as "time of day"; show the strongest
  // once rather than the same words twice.
  const drivers = [];
  for (const item of prediction.top_feature_contributions || []) {
    if (!drivers.some((d) => d.feature === item.feature)) drivers.push(item);
  }

  const reasonGroups = Object.entries(REASON_TITLES)
    .map(([key, title]) => [title, prediction.grouped_reasons?.[key] || []])
    .filter(([, items]) => items.length);

  return (
    <div className="detail">
      <section className="verdict panelCard reveal" style={{ "--i": 0 }}>
        <div className="verdict__top">
          <ScoreRing value={safety} tone={tone} />
          <div className="verdict__text">
            <span className={`chip tone-${tone}`}>{prediction.label}</span>
            <p className="verdict__sentence">{verdictSentence(entry, fastest)}</p>
            <p className="muted tnum verdict__meta1">
              {km(entry.route.distance)} km · {duration(entry.route.duration)}{" "}
              {modeById(entry.mode).noun}
            </p>
          </div>
        </div>

        {/* The walk as a strip: each stretch is a block, coloured by its state. */}
        <div className="stretchStrip" aria-label="Stretches of this route">
          {(prediction.segment_scores || []).map((seg, i) => (
            <span
              key={i}
              className={`stretchStrip__block tone-${labelTone(seg.label)}`}
              style={{ "--i": i }}
              title={`Stretch ${i + 1}: ${seg.label}`}
            />
          ))}
        </div>

        {/*
          How sure we are and how much map data backs it are different
          questions — the score can be certain about a place the map barely
          covers — so both are shown, in words rather than percentages.
        */}
        <dl className="verdict__meta">
          <div>
            <dt className="eyebrow">How sure we are</dt>
            <dd>{confidenceWord(prediction.confidence)}</dd>
          </div>
          <div>
            <dt className="eyebrow">Map data here</dt>
            <dd className={pct(prediction.data_coverage) < 30 ? "is-warn" : undefined}>
              {coverageWord(prediction.data_coverage)}
            </dd>
          </div>
          <div>
            <dt className="eyebrow">Worst stretch</dt>
            {/* Safety, like every other figure on screen — this one
                still read as risk, so a good route reported "Safe · 6%". */}
            <dd>{worst ? `${worst.label} · ${100 - pct(worst.risk_score)}%` : "—"}</dd>
          </div>
          <div>
            <dt className="eyebrow">Biggest concern</dt>
            <dd>{topDriver ? topDriver.feature : "None"}</dd>
          </div>
        </dl>
      </section>

      {liveBySource.size > 0 && (
        <section className="panelCard reveal" style={{ "--i": 1 }}>
          <h3>Live conditions</h3>
          <ul className="liveList">
            {[...liveBySource.entries()].map(([source, adjustment]) => (
              <li key={source} className="liveList__item">
                <div className="liveList__head">
                  <strong>{source}</strong>
                  <span className={`pill${adjustment.data_available ? " is-live" : ""}`}>
                    {adjustment.data_available ? "Live" : "Unavailable"}
                  </span>
                </div>
                <p className="muted">{adjustment.description}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {reasonGroups.length > 0 && (
        <section className="panelCard reveal" style={{ "--i": 2 }}>
          <h3>What we know about this walk</h3>
          {reasonGroups.map(([title, items]) => (
            <div className="reasonGroup" key={title}>
              <h4 className="eyebrow">{title}</h4>
              <ul className="reasonGroup__list">
                {items.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      )}

      {drivers.length > 0 && (
        <section className="panelCard reveal" style={{ "--i": 3 }}>
          <h3>Working for and against you</h3>
          <ul className="drivers">
            {drivers.map((item) => {
              const raises = item.direction === "increases_risk";
              return (
                <li className="drivers__item" key={item.feature}>
                  <span className={`pill ${raises ? "is-up" : "is-down"}`}>
                    {raises ? "Against" : "For"}
                  </span>
                  <span className="drivers__name">{item.feature}</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {(prediction.segment_scores || []).length > 0 && (
        <section className="panelCard reveal" style={{ "--i": 4 }}>
          <h3>Stretch by stretch</h3>
          <p className="panelCard__lede">Safety score for each part of the walk.</p>
          <SegmentList segments={prediction.segment_scores} />
        </section>
      )}

      {worst && <CommunityPanel point={worst.point} />}
    </div>
  );
}
