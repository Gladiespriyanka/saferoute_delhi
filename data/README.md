# Data inputs

SafeHerWay builds its features from two kinds of input.

## 1. OpenStreetMap — fetched automatically

Everything about the *physical environment* is measured from OSM and needs
no manual step:

```bash
python fetch_osm_data.py
```

This downloads, via the keyless public Overpass API, and aggregates into
`artifacts/grid_features.csv` (one row per ~1.2 × 1.4 km cell):

| Feature | Derived from |
|---|---|
| `lighting_score` | share of `lit`-tagged road length tagged `lit=yes`, blended with mapped lamp density |
| `streetlight_density` | `highway=street_lamp` node density |
| `cctv_coverage` | `man_made=surveillance` node density |
| `footpath_quality` | `highway=footway\|pedestrian\|path\|steps\|living_street` length density |
| `crowd_density` | density of shops and civic/commercial amenities, as a **footfall proxy** |
| `osm_coverage` | how much OSM evidence the cell has at all |
| POI distances | real `station=subway`, `highway=bus_stop`, `amenity=hospital\|clinic`, `amenity=police` locations |

Raw Overpass responses are cached under `artifacts/osm_cache/` so a
training run is reproducible and its vintage auditable.

### The coverage caveat

OSM mapping density in Delhi is very uneven — central areas are mapped in
far more detail than the outskirts. **A cell with zero mapped streetlights
is usually an under-mapped cell, not an unlit one.** The pipeline handles
this explicitly rather than pretending otherwise:

- densities are log-compressed against a high percentile, not the max, so a
  few hyper-dense market blocks don't squash the rest of the city;
- the `lit=yes` share is shrunk toward the city-wide share in proportion to
  how little tagged road length a cell actually has;
- `osm_coverage` travels with every prediction and discounts the reported
  confidence where the underlying evidence is thin.

## 2. District crime — you supply this

`delhi_district_crime.csv` is the one input that cannot be fetched. NCRB's
*Crime in India* district tables and the Delhi Police annual reports are
published as PDFs, and data.gov.in's equivalents need a registered API key.

**The file ships as a schema template with every row marked
`source=PLACEHOLDER`, and the system never invents numbers to fill it.**
While it is unpopulated:

- `crime_risk_index` is dropped from the model's feature set entirely,
- its weight in the composite is redistributed over the measured features,
- `/health`, the model card and the frontend all report crime data as
  *not configured*.

So the default shipped model is trained **only** on measured OSM
quantities. Populating this file is a strict upgrade — no code changes.

### How to populate it

| Column | Meaning |
|---|---|
| `district` | must match one of Delhi's 11 revenue districts (see the shipped rows) |
| `year` | the reporting year, e.g. `2022` |
| `source` | anything other than `PLACEHOLDER`, e.g. `NCRB Crime in India 2022, Table 3A.4` |
| `reported_crimes` | reported offence count for that district |
| `population` | district population, used to convert counts to a rate per 100,000 |
| `notes` | free text |

Suggested sources:

- **NCRB, *Crime in India*** — <https://ncrb.gov.in/crime-in-india.html>.
  The district-wise tables for Delhi; "Crimes Against Women" is the most
  relevant offence category for this application.
- **Delhi Police annual report** — <https://delhipolice.gov.in>. Reports
  by *police* district (15 of them), which do not map one-to-one onto the
  11 revenue districts; aggregate before entering.
- **Census of India 2011 / district projections** for `population`.

Counts are converted to a rate per 100,000 residents, min-max scaled across
districts, then mapped onto `[0.15, 0.85]` — the safest district in Delhi is
not a place with zero risk, and the least safe is not certain danger.

After editing, rebuild:

```bash
python train_model.py
```

District boundaries themselves *are* fetched from OSM (`admin_level=5`
relations inside Delhi), so each grid cell is assigned to the district
actually containing it rather than by nearest centroid.
