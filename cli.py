"""
SafeRoute Delhi CLI.

By default this calls the SafeRouteService in-process (no server needed).
Pass --api-url to instead hit a running FastAPI instance over HTTP, which
doubles as a REST API validation tool.

Examples
--------
    python cli.py train
    python cli.py predict --points "28.62,77.21" "28.63,77.22" --hour 23
    python cli.py predict --points "28.62,77.21" --api-url http://localhost:8000 --api-key demo-key-123
    python cli.py compare --route-a "28.62,77.21;28.63,77.22" --route-b "28.60,77.19;28.61,77.20"
    python cli.py feedback --point "28.62,77.21" --rating 2 --comment "Poorly lit lane"
    python cli.py nearby --point "28.62,77.21" --radius 1.5
    python cli.py interactive
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional

import typer

app = typer.Typer(help="SafeRoute Delhi command line tools.")


def _parse_point(text: str):
    from app.schemas import GeoPoint, RouteSegmentInput

    lat_str, lon_str = text.split(",")
    return RouteSegmentInput(point=GeoPoint(lat=float(lat_str), lon=float(lon_str)))


def _parse_route(text: str) -> list:
    return [_parse_point(p) for p in text.split(";")]


def _get_service():
    from app.service import SafeRouteService

    return SafeRouteService()


def _print_predict_result(result: dict) -> None:
    typer.secho(f"\nOverall label: {result['label']}  (risk score: {result['overall_risk_score']:.3f}, "
                f"confidence: {result['confidence']:.3f})", fg=typer.colors.CYAN, bold=True)
    typer.echo("\nContext adjustments:")
    for adj in result["context_adjustments"]:
        avail = "✓" if adj["data_available"] else "✗ (fallback)"
        typer.echo(f"  [{adj['source']:8s}] {avail:14s} adj={adj['adjustment']:+.3f}  {adj['description']}")

    typer.echo("\nTop feature contributions (worst segment):")
    for c in result["top_feature_contributions"]:
        arrow = "▲" if c["direction"] == "increases_risk" else "▼"
        typer.echo(f"  {arrow} {c['feature']:35s} {c['contribution']:+.4f}")

    typer.echo("\nReasons:")
    for group, items in result["grouped_reasons"].items():
        if items:
            typer.echo(f"  {group.title()}:")
            for item in items:
                typer.echo(f"    - {item}")


@app.command()
def train():
    """Train (or retrain) the model from freshly generated synthetic data."""
    service = _get_service()
    typer.echo("Generating synthetic training data and training model...")
    metrics = service.bootstrap()
    typer.secho(f"Done. Test accuracy={metrics['test_accuracy']:.3f}, "
                f"mean confidence={metrics['mean_confidence']:.3f}, "
                f"n_train={metrics['n_train']}, n_test={metrics['n_test']}", fg=typer.colors.GREEN)


@app.command()
def predict(
    points: list[str] = typer.Option(..., "--points", help='Segment points as "lat,lon" (repeatable)'),
    hour: Optional[int] = typer.Option(None, help="Hour of day (0-23) to evaluate at; defaults to now"),
    no_live_context: bool = typer.Option(False, help="Disable live weather/traffic enrichment"),
    api_url: Optional[str] = typer.Option(None, help="If set, call the REST API instead of running in-process"),
    api_key: str = typer.Option("demo-key-123", help="API key (only used with --api-url)"),
):
    """Score a route made of one or more lat,lon points."""
    when = None
    if hour is not None:
        now = datetime.now()
        when = now.replace(hour=hour, minute=0, second=0, microsecond=0)

    if api_url:
        import httpx

        payload = {
            "segments": [{"point": {"lat": float(p.split(",")[0]), "lon": float(p.split(",")[1])}} for p in points],
            "use_live_context": not no_live_context,
        }
        if when:
            payload["timestamp"] = when.isoformat()
        resp = httpx.post(f"{api_url}/predict", json=payload, headers={"x-api-key": api_key}, timeout=15)
        if resp.status_code != 200:
            typer.secho(f"API error {resp.status_code}: {resp.text}", fg=typer.colors.RED)
            raise typer.Exit(1)
        data = resp.json()
        typer.secho(f"\nOverall label: {data['label']} (risk={data['overall_risk_score']:.3f}, "
                    f"confidence={data['confidence']:.3f})", fg=typer.colors.CYAN, bold=True)
        for group, items in data["grouped_reasons"].items():
            if items:
                typer.echo(f"  {group.title()}:")
                for item in items:
                    typer.echo(f"    - {item}")
        return

    service = _get_service()
    segments = [_parse_point(p) for p in points]
    result = service.score_route(segments, when, not no_live_context)
    _print_predict_result(result)


@app.command()
def compare(
    route_a: str = typer.Option(..., help='Route A points, e.g. "28.62,77.21;28.63,77.22"'),
    route_b: str = typer.Option(..., help="Route B points"),
    route_c: Optional[str] = typer.Option(None, help="Optional Route C points"),
    hour: Optional[int] = typer.Option(None, help="Hour of day (0-23)"),
):
    """Compare two or three candidate routes and get a recommendation."""
    service = _get_service()
    routes = {"Route A": _parse_route(route_a), "Route B": _parse_route(route_b)}
    if route_c:
        routes["Route C"] = _parse_route(route_c)

    when = None
    if hour is not None:
        now = datetime.now()
        when = now.replace(hour=hour, minute=0, second=0, microsecond=0)

    comparison = service.compare_routes(routes, when, True)
    typer.secho(f"\nRecommended: {comparison['recommended_route']}", fg=typer.colors.GREEN, bold=True)
    for name, res in comparison["results"].items():
        typer.echo(f"  {name}: {res['label']} (risk={res['overall_risk_score']:.3f}, confidence={res['confidence']:.3f})")


@app.command()
def feedback(
    point: str = typer.Option(..., help='"lat,lon"'),
    rating: int = typer.Option(..., min=1, max=5, help="1=very unsafe .. 5=very safe"),
    comment: Optional[str] = typer.Option(None),
):
    """Submit a crowdsourced safety audit for a location."""
    service = _get_service()
    lat, lon = (float(x) for x in point.split(","))
    result = service.submit_feedback(lat, lon, rating, comment, None)
    typer.secho(f"Recorded audit {result['audit_id']} for {result['area_code']}. "
                f"Updated area audit score: {result['updated_area_audit_score']:.3f}", fg=typer.colors.GREEN)


@app.command()
def nearby(
    point: str = typer.Option(..., help='"lat,lon"'),
    radius: float = typer.Option(1.0, help="Search radius in km"),
):
    """List crowdsourced audits near a point."""
    service = _get_service()
    lat, lon = (float(x) for x in point.split(","))
    records = service.nearby_audits(lat, lon, radius)
    if not records:
        typer.echo("No audits found nearby.")
        return
    for r in records:
        typer.echo(f"  [{r['distance_km']:.2f}km] rating={r['rating']} area={r['area_code']} comment={r.get('comment','')}")


@app.command()
def interactive():
    """Drop into a simple interactive prompt loop for ad-hoc queries."""
    service = _get_service()
    typer.secho("SafeRoute Delhi interactive CLI. Type 'help' for commands, 'quit' to exit.", fg=typer.colors.CYAN)
    while True:
        try:
            line = input("saferoute> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in ("quit", "exit"):
            break
        if line == "help":
            typer.echo("Commands: predict <lat,lon>[;lat,lon...] | feedback <lat,lon> <rating> | quit")
            continue
        parts = line.split()
        try:
            if parts[0] == "predict" and len(parts) >= 2:
                segments = _parse_route(parts[1])
                result = service.score_route(segments, None, True)
                _print_predict_result(result)
            elif parts[0] == "feedback" and len(parts) >= 3:
                lat, lon = (float(x) for x in parts[1].split(","))
                rating = int(parts[2])
                res = service.submit_feedback(lat, lon, rating, None, None)
                typer.echo(f"Recorded. Area {res['area_code']} audit score now {res['updated_area_audit_score']:.3f}")
            else:
                typer.echo("Unrecognized command. Type 'help'.")
        except Exception as exc:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED)


if __name__ == "__main__":
    app()
