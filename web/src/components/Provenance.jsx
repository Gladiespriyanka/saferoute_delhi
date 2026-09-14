/**
 * Where the score comes from, in one breath. The accuracy and calibration
 * figures live in /health and the model card for anyone who wants them;
 * they aren't what a person deciding which street to take needs to read.
 */
export default function Provenance({ status, health }) {
  if (status === "loading") {
    return <footer className="provenance"><p>Connecting…</p></footer>;
  }
  if (status === "error" || !health) {
    return (
      <footer className="provenance provenance--warn">
        <p>Can't reach the scoring service — routes can't be checked right now.</p>
      </footer>
    );
  }
  const synthetic = health.feature_source !== "openstreetmap";
  return (
    <footer className="provenance">
      {synthetic ? (
        <p className="provenance__warn">Demo data — scores here are illustrative only.</p>
      ) : (
        <p>Street data from OpenStreetMap, updated {health.osm_snapshot}.</p>
      )}
      {!health.model_loaded && (
        <p className="provenance__warn">Scoring isn't set up yet on this server.</p>
      )}
      <p className="provenance__caveat">
        Scores are estimates from what's known about the streets. They can't see who is
        there tonight — trust your own judgement first.
      </p>
    </footer>
  );
}
