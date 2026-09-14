"""
Test fixtures.

Two things are set up before anything from `app` is imported, which is why
they live at module scope in conftest rather than in a fixture:

* `SAFEROUTE_ARTIFACTS_DIR` points at a temporary directory, so the suite
  never reads or overwrites the real model, the OSM cache or the audit log
  in the checkout.
* `SAFEROUTE_GRID_CELLS` shrinks the analysis grid, so the synthetic city
  the tests run against builds in well under a second.

The suite deliberately runs against synthetic city data (app/synthetic.py).
It emits exactly the same tables as the real OpenStreetMap extract, so every
code path under test is the production one -- but tests stay hermetic, fast
and don't hammer a public Overpass mirror.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_ARTIFACTS = Path(tempfile.mkdtemp(prefix="saferoute-tests-"))
os.environ["SAFEROUTE_ARTIFACTS_DIR"] = str(_TEST_ARTIFACTS)
os.environ.setdefault("SAFEROUTE_GRID_CELLS", "12")
os.environ.setdefault("SAFEROUTE_API_KEYS", "demo-key-123,dev-key-456")

import pytest  # noqa: E402

from app.dataset import build_training_dataset, load_delhi_data  # noqa: E402
from app.features import model_feature_columns  # noqa: E402
from app.model import save_model, train_model  # noqa: E402
from app.schemas import GeoPoint, RouteSegmentInput  # noqa: E402

API_KEY = "demo-key-123"


@pytest.fixture(scope="session")
def city_data():
    """The synthetic city, built once for the whole session."""
    return load_delhi_data(allow_synthetic=True)


@pytest.fixture(scope="session")
def training_frame(city_data):
    return build_training_dataset(city_data, n_samples=2500, seed=7)


@pytest.fixture(scope="session")
def trained_model(city_data, training_frame):
    """A real trained + calibrated model, saved to the temp artifacts dir."""
    columns = model_feature_columns(city_data.with_crime)
    model, metrics = train_model(training_frame, columns, provenance=city_data.describe(), seed=7)
    save_model(model, metrics)
    return model


@pytest.fixture(scope="session")
def service(trained_model):
    """A SafeRouteService backed by the model above."""
    from app.service import SafeRouteService

    return SafeRouteService()


@pytest.fixture(scope="session")
def client(service):
    """
    A TestClient whose app shares the session's service instance.

    The lifespan handler builds its own service on startup; both the module
    global (which /health reads) and the injected dependency are pointed back
    at the fixture's instance afterwards, so an audit submitted through the
    API is visible to a test that queries the service directly.
    """
    from fastapi.testclient import TestClient

    import app.main as main

    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        main.service = service
        main.app.dependency_overrides[main.get_service] = lambda: service
        try:
            yield test_client
        finally:
            main.app.dependency_overrides.clear()


def segment(lat: float, lon: float, **overrides) -> RouteSegmentInput:
    return RouteSegmentInput(point=GeoPoint(lat=lat, lon=lon), **overrides)
