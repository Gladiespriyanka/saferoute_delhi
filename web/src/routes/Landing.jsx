import { Link } from "react-router-dom";


const STEPS = [
  {
    n: "01",
    title: "Tell it where you're going",
    body: "Two pins, or one tap if you've saved home. It defaults to leaving right now.",
  },
  {
    n: "02",
    title: "Every option gets scored",
    body: "Each way there is split into stretches and rated on lighting, how busy it is, footpaths and how far you are from help.",
  },
  {
    n: "03",
    title: "Live conditions adjust it",
    body: "Current weather and traffic nudge the score up or down, capped so they can never override the route itself.",
  },
  {
    n: "04",
    title: "Save it, and hear about changes",
    body: "Keep the walks you do often. When someone reports a problem along one, it's waiting for you on My routes.",
  },
];

const SIGNALS = [
  "Street lighting",
  "Surveillance coverage",
  "Footpath provision",
  "How busy the area is",
  "Distance to metro, bus, hospital, police",
  "Time of day",
  "Live weather",
  "Traffic congestion",
  "Community reports",
];

export default function Landing() {
  return (
    <div className="landing">
      <header className="landing__nav onDark">
        <div className="landing__navInner">
          <span className="landing__brand">SafeHerWay</span>
          <Link className="btn btn--onDark" to="/plan">
            Find a safer route
          </Link>
        </div>
      </header>

      <section className="hero onDark">
        <div className="hero__inner">
          <div className="hero__copy">
            <p className="eyebrow hero__eyebrow">Delhi</p>
            <h1 className="hero__title">Get home the safest way, not just the fastest.</h1>
            <p className="hero__lede">
              Compare every way to get somewhere — on foot, by bike, or in a cab — by how well
              lit and how busy the streets are, and how far you are from help. Save the routes
              you use most and hear when someone reports a problem along them.
            </p>
            <ul className="hero__modes" aria-label="Ways to travel">
              <li>On foot</li>
              <li>By bike</li>
              <li>By cab or auto</li>
            </ul>

            <div className="hero__actions">
              <Link className="btn btn--primary btn--lg" to="/plan">
                Find a safer route
              </Link>
              <a className="hero__link" href="#how">
                See how it works
              </a>
            </div>
            <dl className="hero__stats">
              <div>
                <dt>Every stretch</dt>
                <dd>scored, not just the whole route</dd>
              </div>
              <div>
                <dt>Your routes</dt>
                <dd>saved and watched for new reports</dd>
              </div>
              <div>
                <dt>One tap</dt>
                <dd>to emergency help, always</dd>
              </div>
            </dl>
          </div>

          <aside className="heroCard" aria-label="Example result">
            <div className="heroCard__head">
              <span className="chip tone-safe">Safest route</span>
              <span className="muted tnum">2.1 km · 26 min</span>
            </div>
            <p className="heroCard__score">
              <span className="tnum">82</span>
              <span className="heroCard__pct">%</span>
              <span className="eyebrow heroCard__caption">safety score</span>
            </p>
            <p className="heroCard__verdict">
              6 min longer than the quickest route, for 31 points less risk.
            </p>
            <ul className="heroCard__rows">
              <li>
                <span>Hauz Khas Village</span>
                <span className="chip chip--bare tone-safe">Safe</span>
              </li>
              <li>
                <span>Aurobindo Marg underpass</span>
                <span className="chip chip--bare tone-moderate">Moderate</span>
              </li>
              <li>
                <span>GK II market</span>
                <span className="chip chip--bare tone-safe">Safe</span>
              </li>
            </ul>
          </aside>
        </div>
      </section>

      <section className="section" id="how">
        <div className="section__inner">
          <p className="eyebrow">How it works</p>
          <h2 className="section__title">From two pins to a safer route</h2>
          <ol className="steps">
            {STEPS.map((step) => (
              <li className="step" key={step.n}>
                <span className="step__n tnum">{step.n}</span>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="section section--dark onDark">
        <div className="section__inner section__split section__split--reverse">
          <div className="routesPreview" aria-label="My routes preview">
            <div className="routesPreview__head">
              <span className="eyebrow routesPreview__eyebrow">My routes</span>
              <span className="routesPreview__badge">2 new</span>
            </div>
            <div className="routesPreview__card is-alert">
              <div className="routesPreview__row">
                <div>
                  <strong>Home from the office</strong>
                  <span>Connaught Place → Hauz Khas · On foot</span>
                </div>
                <b className="tnum tone-moderate">65%</b>
              </div>
              <div className="routesPreview__report">
                <span className="chip chip--bare tone-unsafe">Felt unsafe</span>
                <span>Poorly lit · Was followed</span>
                <em>2 hours ago</em>
              </div>
            </div>
            <div className="routesPreview__card">
              <div className="routesPreview__row">
                <div>
                  <strong>Gym, late</strong>
                  <span>Green Park → Safdarjung · By bike</span>
                </div>
                <b className="tnum tone-safe">91%</b>
              </div>
            </div>
            <div className="routesPreview__card">
              <div className="routesPreview__row">
                <div>
                  <strong>Station run</strong>
                  <span>Lajpat Nagar → Nizamuddin · By cab</span>
                </div>
                <b className="tnum tone-safe">88%</b>
              </div>
            </div>
          </div>
          <div>
            <p className="eyebrow section__eyebrowOnDark">Your routes, watched</p>
            <h2 className="section__title section__title--onDark">
              Save the routes you take. Hear when they change.
            </h2>
            <p className="section__lede section__lede--onDark">
              Every saved route is re-checked each time you open the app, and every report
              someone leaves along it — a stretch that's gone dark, a place they felt
              followed — is flagged for you. It's a shared record of the streets, built by
              the people who use them.
            </p>
            <Link className="btn btn--onDark section__cta" to="/routes">
              Open My routes
            </Link>
          </div>
        </div>
      </section>

      <section className="section section--alt">
        <div className="section__inner section__split">
          <div>
            <p className="eyebrow">What goes into a score</p>
            <h2 className="section__title">What a score is made of</h2>
            <p className="section__lede">
              Every stretch of a route is scored on explainable signals, and you can see
              exactly which ones pushed the number up or down.
            </p>
            <ul className="signals">
              {SIGNALS.map((signal) => (
                <li key={signal}>{signal}</li>
              ))}
            </ul>
            <p className="section__note">
              Street details come from OpenStreetMap for the exact spot, weather and traffic
              are live when you search, and reports come from people who walked it.
            </p>
          </div>

          <div className="driversCard">
            <p className="eyebrow">For and against, on one walk</p>
            <ul>
              <li>
                <span className="pill is-up">Against</span> Low streetlight density along this
                stretch
              </li>
              <li>
                <span className="pill is-down">For</span> Busy market frontage most of the
                way
              </li>
              <li>
                <span className="pill is-up">Against</span> Light rain forecast at your
                departure time
              </li>
              <li>
                <span className="pill is-down">For</span> A police station within 400 m
              </li>
            </ul>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section__inner section__narrow">
          <p className="eyebrow">Good to know</p>
          <h2 className="section__title">What it can and can't see</h2>
          <ul className="limits">
            <li>
              <strong>It knows the streets, not who's on them.</strong> Scores come from
              what's mapped and reported — lighting, footpaths, how busy an area usually is.
              Trust your own eyes first.
            </li>
            <li>
              <strong>Some areas are better mapped than others.</strong> Every result says how
              much data actually backs it.
            </li>
            <li>
              <strong>Your details stay on your phone.</strong> Saved routes, home and your
              trusted contact never leave your device.
            </li>
          </ul>
        </div>
      </section>

      <section className="cta onDark">
        <div className="cta__inner">
          <h2>Know the way before you take it.</h2>
          <p>Two pins, a few seconds. Free, no sign-up.</p>
          <Link className="btn btn--primary btn--lg" to="/plan">
            Find a safer route
          </Link>
        </div>
      </section>

      <footer className="landing__footer onDark">
        <div className="landing__footerInner">
          <span>SafeHerWay</span>
          <span className="landing__footerLinks">
            <a href="tel:112">Police — 112</a>
            <a href="tel:1091">Women's helpline — 1091</a>
          </span>
        </div>
      </footer>
    </div>
  );
}
