# Third-party software and reference data

The root [LICENSE](LICENSE) covers only first-party material to the extent the respective rights holders can license it. It does not replace upstream licenses or restrict rights those licenses grant. Public availability of a dataset alone is not proof of redistribution permission.

## Bundled software and assets

| Paths | Attribution and terms |
|---|---|
| `web/vendor/leaflet/` | Leaflet 1.9.4, BSD-2-Clause; preserve [bundled license](web/vendor/leaflet/LICENSE). |
| `web/vendor/components/lastmile-ui.js`, `lastmile-story.js` | Bundled Web Awesome, Dagre, Lit and other dependencies retain their generated [license notices](web/vendor/components/LICENSES.txt), including MIT/BSD notices. `scripts/build-ui.mjs` regenerates/checks them. |
| `web/vendor/components/icons/` | Font Awesome Free 7.0.0, Fonticons, Inc.; each SVG retains its attribution/license comment. See [Font Awesome Free license](https://fontawesome.com/license/free), including the icon artwork terms; package notices do not replace asset-specific terms. |
| `lastmile/schema/CAP-v1.2.xsd` | Copyright OASIS Open 2010; embedded notice retained. Applicable schema redistribution notice must be confirmed before public release. [CAP 1.2 specification](https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html). |

Python/Node dependencies installed from their package registries retain their own licenses. They are not relicensed by this repository. The package manifests/lock identify direct dependencies; a complete transitive dependency/license review remains part of the public-release checks. No dependency environment or `node_modules` is committed.

## Geographic and operational reference data

| Paths | Attribution and scope |
|---|---|
| `web/data/sri-lanka-districts.geojson`, OSM-derived district references | OpenStreetMap contributors / geoBoundaries metadata; ODbL 1.0. Preserve provenance, attribution and applicable derivative-database obligations. |
| `web/data/sri-lanka-divisions.geojson` and division derivatives | OCHA ROAP / Survey Department of Sri Lanka, via geoBoundaries; CC BY 3.0 IGO per the recorded upstream metadata. |
| `web/data/sri-lanka-areas*`, `data/boundaries/lk.geojson` | Derived reference layers retain the relevant upstream licenses and documented modifications; see [map provenance](web/data/README.md). |
| `data/reference/health.json`, `lifelines.json`, `vulnerable.json` | OpenStreetMap contributors, via HOT/HDX exports; ODbL per embedded provenance. |
| `data/reference/population.json`, `gn.json` | Department of Census and Statistics / UNFPA and Survey Department / OCHA, via HDX; CC BY-IGO as recorded. Confirm exact resource/version terms before public release. |
| `data/reference/gauges.json` | Irrigation Department bulletin; attribution recorded, redistribution permission not established solely by the current provenance text. |
| `data/reference/history.json` | Disaster Management Centre / DesInventar / UNDRR; attribution recorded, redistribution permission still requires confirmation. |
| `data/reference/items.json` | Prototype item catalog with Sphere-derived standard summaries; confirm applicable excerpt/derivative terms before public release. |
| Other `data/reference/` seeds | Fictional centres, quantities, generic desks/media and prototype templates, plus public hotline references. Review exact provenance/content; no real operational affiliation is claimed. |

See [reference data notes](data/reference/README.md) for dataset-level sources, modifications and limitations. Third-party data must not be described as covered by a restrictive first-party reuse ban. If permission for an included reference is not established, clear it or replace it with compatible, explicitly fictional data before publishing the repository.

## Publication and unresolved provenance

This curated repository is published for competition evaluation at the maintainer's direction. Publication does not establish that every schema/dataset/transitive-license question above has been resolved, and it grants no additional rights in upstream material. Existing upstream copyright notices and previously granted licenses remain effective on their own terms. The first-party notice does not replace them.
