# Sri Lanka geographic references

Retrieved 15 October 2026 through the [geoBoundaries API](https://www.geoboundaries.org/api.html). Exact upstream commit URLs, downloaded-byte SHA-256 hashes and original metadata are in [geography-provenance.json](geography-provenance.json).

| File | Source / year represented | Licence |
| --- | --- | --- |
| `sri-lanka-districts.geojson` (25 districts) | geoBoundaries gbOpen LKA ADM2; OpenStreetMap / Wambacher; **2017** | [Open Database Licence 1.0](https://opendatacommons.org/licenses/odbl/1-0/); [OpenStreetMap attribution](https://www.openstreetmap.org/copyright) |
| `sri-lanka-divisions.geojson` (330 divisions) | geoBoundaries gbOpen LKA ADM3; OCHA ROAP / Survey Department of Sri Lanka; **2020** | [CC BY 3.0 IGO](https://creativecommons.org/licenses/by/3.0/igo/); [original dataset](https://data.humdata.org/dataset/sri-lanka-administrative-levels-0-4-boundaries) |

These are simplified geographic reference datasets, not certified current warning boundaries, facility records or road-access data. Local modifications add `reference_only: true` and a normalized `name` property; geometry is retained from the downloaded simplified files. This database is offered under its stated upstream licences; the repository's software licence does not replace them. No endorsement by any source is implied.

`data/boundaries/lk.geojson` is a derived subset matched to the existing starter gazetteer (retains upstream geometry). `boundary-matches.json` records the matching results and missing entries. **23 of 24 gazetteer places match**; Kuruwita (`LK-RA-KUR`) retains the engine's explicitly approximate circle fallback. The complete 25-district reference backdrop is independent of that limited warning gazetteer. A drawn district does not mean it is supported by a warning pack.

Centre names and markers in the exercise are **fictional**, with no implied approval, real capacity or actual admission availability. The map does not calculate safe routes. Warning layers are exercise replay coverage; the public map filters publication validity at its selected time. No active warning displayed is not an all-clear.

Leaflet 1.9.4 is bundled in `web/vendor/leaflet/`, with its BSD-2-Clause licence. Street tiles are optional, off by default, and requested from OpenStreetMap only when enabled in the workspace. Follow [OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/) when adapting deployment traffic or tile providers.

Refresh assets deliberately with `python scripts/fetch_workspace_assets.py`; the application never downloads them at startup. Review new metadata, licences and matching results after each refresh.

`sri-lanka-areas.geojson` and `sri-lanka-areas.json` are derived by `python scripts/build_area_index.py`. They contain every DS division with a stable ID (`ds-<district>-<name>`) and the district holding most of its area, simplified to about 150 m for drawing, plus district centres and bounds. They inherit the licences above and are reference outlines, not certified warning zones.
