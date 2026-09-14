"""
SafeHerWay command line tools.

By default every command runs the pipeline in-process -- no server needed.
Pass `--api-url` to `predict` to hit a running FastAPI instance over HTTP
instead, which doubles as a REST API smoke test.

    python cli.py info
    python cli.py predict --points "28.62,77.21" --points "28.63,77.22" --hour 23
    python cli.py compare --route "Ring Rd=28.62,77.21;28.63,77.22" --route "Lane=28.60,77.19"
    python cli.py feedback --point "28.62,77.21" --rating 2 --comment "Poorly lit lane"
    python cli.py nearby --point "28.62,77.21" --radius 1.5
    python cli.py metrics
    python cli.py interactive
"""
from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache

import typer

cli = typer.Typer(help=__doc__, no_args_is_help=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _parse_point(text: str):
    """Parse "lat,lon" into a RouteSegmentInput, with a usable error message."""
    from app.schemas import GeoPoint, RouteSegmentInput

    parts = text.split(",")
    if len(parts) != 2:
        _fail(f'Could not parse point {text!r}. Expected "lat,lon", e.g. "28.6139,77.2090".')
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        _fail(f'Could not parse point {text!r}. Both values must be numbers.')
    return RouteSegmentInput(point=GeoPoint(lat=lat, lon=lon))


def _parse_route(text: str) -> list:
    return [_parse_point(part) for part in text.split(";") if part.strip()]


def _parse_named_route(text: str) -> tuple[str, list]:
    """Parse "Name=lat,lon;lat,lon"; the name is optional."""
    name, separator, points = text.partition("=")
    if not separator:
        name, points = f"Route {text[:12]}", text
    return name.strip(), _parse_route(points)


def _when(hour: int | None) -> datetime | None:
    """Today at the given hour, or None for 'now'."""
    if hour is None:
        return None
    if not 0 <= hour <= 23:
        _fail("--hour must be between 0 and 23.")
    return datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)


@lru_cache(maxsize=1)
def _service():
    """Build the service once per process -- it loads the model and indices."""
    from app.service import SafeRouteService

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        return SafeRouteService()
    except Exception as exc:  # noqa: BLE001
        _fail(f"Could not start the service: {exc}")


def _bar(value: float, width: int = 24) -> str:
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "█" * filled + "·" * (width - filled)


LABEL_COLORS = {
    "Safe": typer.colors.GREEN,
    "Moderate": typer.colors.YELLOW,
    "Unsafe": typer.colors.RED,
}


def _print_route(result: dict) -> None:
    label = result["label"]
    typer.secho(
        f"\n  {label.upper()}   risk {result['overall_risk_score']:.0%}  "
        f"{_bar(result['overall_risk_score'])}",
        fg=LABEL_COLORS.get(label, typer.colors.WHITE),
        bold=True,
    )
    typer.echo(
        f"  model confidence {result['confidence']:.0%}   "
        f"map data coverage {result['data_coverage']:.0%}"
    )

    typer.secho("\n  Live context", bold=True)
    for adjustment in result["context_adjustments"]:
        mark = "live" if adjustment["data_available"] else "n/a "
        typer.echo(
            f"    [{adjustment['source']:7s}] {mark}  {adjustment['adjustment']:+.3f}  "
            f"{adjustment['description']}"
        )

    typer.secho("\n  Top model factors (worst segment)", bold=True)
    for contribution in result["top_feature_contributions"]:
        arrow = "▲" if contribution["direction"] == "increases_risk" else "▼"
        colour = (
            typer.colors.RED
            if contribution["direction"] == "increases_risk"
            else typer.colors.GREEN
        )
        typer.secho(
            f"    {arrow} {contribution['feature']:38s} {contribution['contribution']:+.4f}",
            fg=colour,
        )

    typer.secho("\n  Why", bold=True)
    any_reason = False
    for group, items in result["grouped_reasons"].items():
        if not items:
            continue
        any_reason = True
        typer.secho(f"    {group.title()}", bold=True)
        for item in items:
            typer.echo(f"      - {item}")
    if not any_reason:
        typer.echo("    Nothing notable flagged for this route.")
    typer.echo("")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@cli.command()
def info():
    """Show what data and model this installation is running on."""
    status = _service().status()
    typer.secho("SafeHerWay", fg=typer.colors.CYAN, bold=True)
    for key, value in status.items():
        typer.echo(f"  {key:20s} {value}")
    if status["feature_source"] != "openstreetmap":
        typer.secho(
            "\n  Running on SYNTHETIC data. Run `python fetch_osm_data.py` then "
            "`python train_model.py` for real OpenStreetMap measurements.",
            fg=typer.colors.YELLOW,
        )


@cli.command()
def metrics():
    """Print the saved model card."""
    from app.config import MODEL_CARD_PATH

    if not MODEL_CARD_PATH.exists():
        _fail(f"No model card at {MODEL_CARD_PATH}. Run `python train_model.py` first.")
    typer.echo(MODEL_CARD_PATH.read_text())


@cli.command()
def predict(
    points: list[str] = typer.Option(..., "--points", "-p",
                                     help='Segment point as "lat,lon" (repeatable)'),
    hour: int | None = typer.Option(None, help="Hour of day 0-23; defaults to now"),
    no_live_context: bool = typer.Option(False, help="Skip live weather/traffic enrichment"),
    api_url: str | None = typer.Option(None, help="Call a running REST API instead of "
                                                     "running in-process"),
    api_key: str = typer.Option("demo-key-123", help="API key (only used with --api-url)"),
):
    """Score a route made of one or more lat,lon points."""
    when = _when(hour)
    segments = [_parse_point(point) for point in points]

    if api_url:
        import httpx

        payload = {
            "segments": [s.model_dump(mode="json") for s in segments],
            "use_live_context": not no_live_context,
        }
        if when:
            payload["timestamp"] = when.isoformat()
        try:
            response = httpx.post(
                f"{api_url.rstrip('/')}/predict",
                json=payload,
                headers={"x-api-key": api_key},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            _fail(f"Could not reach {api_url}: {exc}")
        if response.status_code != 200:
            _fail(f"API error {response.status_code}: {response.text}")
        _print_route(response.json())
        return

    _print_route(_service().score_route(segments, when, not no_live_context))


@cli.command()
def compare(
    route: list[str] = typer.Option(
        ..., "--route", "-r",
        help='A route as "Name=lat,lon;lat,lon" (repeat for each route to compare)',
    ),
    hour: int | None = typer.Option(None, help="Hour of day 0-23"),
    no_live_context: bool = typer.Option(False, help="Skip live weather/traffic enrichment"),
):
    """Compare two or more candidate routes and get a recommendation."""
    if len(route) < 2:
        _fail("Pass --route at least twice so there is something to compare.")
    routes = dict(_parse_named_route(text) for text in route)

    comparison = _service().compare_routes(routes, _when(hour), not no_live_context)
    typer.secho(f"\nRecommended: {comparison['recommended_route']}",
                fg=typer.colors.GREEN, bold=True)
    ordered = sorted(comparison["results"].items(), key=lambda kv: kv[1]["overall_risk_score"])
    for name, result in ordered:
        marker = "*" if name == comparison["recommended_route"] else " "
        typer.secho(
            f" {marker} {name:22s} {result['label']:9s} risk {result['overall_risk_score']:.0%}  "
            f"{_bar(result['overall_risk_score'], 16)}  confidence {result['confidence']:.0%}",
            fg=LABEL_COLORS.get(result["label"]),
        )
    typer.echo("")


@cli.command()
def feedback(
    point: str = typer.Option(..., help='"lat,lon"'),
    rating: int = typer.Option(..., min=1, max=5, help="1 = felt very unsafe .. 5 = very safe"),
    comment: str | None = typer.Option(None),
):
    """Submit a crowdsourced safety audit for a location."""
    segment = _parse_point(point)
    result = _service().submit_feedback(
        segment.point.lat, segment.point.lon, rating, comment, None
    )
    typer.secho(f"Recorded audit {result['audit_id']} for {result['area_code']}.",
                fg=typer.colors.GREEN)
    typer.echo(f"  Area adjustment now {result['adjustment']:+.4f}")
    if result["updated_area_audit_score"] is not None:
        typer.echo(f"  Audit-adjusted crime risk: {result['updated_area_audit_score']:.3f}")
    else:
        typer.echo("  (No district crime data configured, so only the adjustment is tracked.)")


@cli.command()
def nearby(
    point: str = typer.Option(..., help='"lat,lon"'),
    radius: float = typer.Option(1.0, help="Search radius in km"),
):
    """List crowdsourced audits near a point."""
    segment = _parse_point(point)
    records = _service().nearby_audits(segment.point.lat, segment.point.lon, radius)
    if not records:
        typer.echo("No audits found nearby.")
        return
    for record in records:
        stars = "★" * int(record["rating"]) + "☆" * (5 - int(record["rating"]))
        typer.echo(
            f"  [{record['distance_km']:.2f} km] {stars}  {record['area_code']}  "
            f"{record.get('comment', '')}"
        )


@cli.command()
def interactive():
    """Simple prompt loop for ad-hoc queries."""
    service = _service()
    typer.secho("SafeHerWay interactive CLI. 'help' for commands, 'quit' to exit.",
                fg=typer.colors.CYAN)
    while True:
        try:
            line = input("safeherway> ").strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("")
            break
        if not line:
            continue
        if line in ("quit", "exit"):
            break
        if line == "help":
            typer.echo(
                "  predict <lat,lon>[;lat,lon...] [hour]\n"
                "  feedback <lat,lon> <rating 1-5>\n"
                "  info\n"
                "  quit"
            )
            continue

        parts = line.split()
        try:
            if parts[0] == "predict" and len(parts) >= 2:
                hour = int(parts[2]) if len(parts) > 2 else None
                _print_route(service.score_route(_parse_route(parts[1]), _when(hour), True))
            elif parts[0] == "feedback" and len(parts) >= 3:
                segment = _parse_point(parts[1])
                result = service.submit_feedback(
                    segment.point.lat, segment.point.lon, int(parts[2]), None, None
                )
                typer.echo(f"  Recorded for {result['area_code']} "
                           f"(adjustment {result['adjustment']:+.4f})")
            elif parts[0] == "info":
                for key, value in service.status().items():
                    typer.echo(f"  {key:20s} {value}")
            else:
                typer.echo("Unrecognized command. Type 'help'.")
        except typer.Exit:
            continue
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            typer.secho(f"Error: {exc}", fg=typer.colors.RED)


if __name__ == "__main__":
    cli()
