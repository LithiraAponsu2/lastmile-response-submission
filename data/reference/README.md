# Reference seed: places, stock and standards

These files are the starting lists behind **Places & stock** in the app. `python scripts/build_reference_seed.py` builds them (it needs network access). The app copies any missing list into `<data dir>/reference/` on start. When `LASTMILE_STORAGE_BUCKET` is set it also copies them to the private bucket under `state/reference/`. After that, people edit the lists in the app: every change is a new version, with who made it, when, why, and the record before and after.

| File | Records | Real or fictional | Source and licence |
|---|---|---|---|
| `centres.json` | 53 safety centres covering all 25 districts | **Fictional** buildings in real DS divisions (plain names; each record has `fictional: true`) | Demo data |
| `stock.json` | 88 stock lines: an NDRSC store in every district, NWSDB and RDA yards in every province, the forces and a partner (two lines are pledges) | **Fictional** quantities and stores | Demo data |
| `items.json` | 22 relief items with units and minimum standards | Item names are ours; standards summarise Sphere | [Sphere Handbook 2018](https://spherestandards.org/handbook/) |
| `contacts.json` | 4 national hotlines and 30 duty desks (a DDMCU desk in every district) | Hotlines are real public numbers (117, 119, 1990, 110); desks are **fictional** and have no numbers | Public numbers |
| `population.json` | 25 districts, 2023 projection, with under-5 and 65+ counts and Sinhala/Tamil names | Real | [HDX COD-PS](https://data.humdata.org/dataset/cod-ps-lka), Department of Census and Statistics / UNFPA, CC BY-IGO |
| `health.json` | 784 named hospitals with DS division and type | Real, community-mapped | [OpenStreetMap via HOT export on HDX](https://data.humdata.org/dataset/hotosm_lka_health_facilities), © OpenStreetMap contributors, ODbL 1.0 |
| `gauges.json` | 39 river gauging stations with alert, minor and major flood levels | Real thresholds; positions approximate | Irrigation Department "Water Level & Rainfall" bulletin of 8 August 2026; positions from GN division centres (COD-AB) or OpenStreetMap |
| `gn.json` | 14,043 GN divisions with Sinhala names, DS division and centre point | Real | [HDX COD-AB](https://data.humdata.org/dataset/cod-ab-lka), Survey Department / OCHA, CC BY-IGO |
| `history.json` | Floods, landslides, wind, drought and coastal events since 2000, with deaths, people affected and houses, for 320 DS divisions and 25 district-only rows | Real, recorded events | [DesInventar Sri Lanka](https://www.desinventar.net/DesInventar/profiletab.jsp?countrycode=lka), maintained by the DMC |
| `lifelines.json` | 681 water works, wastewater plants, telecom towers, named bridges, police and fire stations | Real, community-mapped | OpenStreetMap points of interest via HOT export, ODbL |
| `templates.json` | 10 message templates: six SMS, two emails, two press releases, in English | Written for this prototype; placeholders are filled from the incident | Demo data |
| `media.json` | 15 media desks (television, radio, papers, online, a news agency, government information) | **Generic** desks, not real outlets; no addresses | Demo data |
| `vulnerable.json` | 152 homes for elders and children, childcare and other care homes | Real, community-mapped; types read from names | OpenStreetMap via HOT export, ODbL |

## What the data does not say

- **Centre capacity here is planning data.** "Places by standard" is the lowest of the design capacity, floor area ÷ 3.5 m² per person, and working toilets × 20 people. It is not today's free places. Live occupancy and admission stay inside each incident, backed by evidence.
- **Hospital types are read from names** ("Base Hospital", "Divisional Hospital", and so on). Beds are rarely mapped. This is not a Ministry of Health register.
- **Gauge positions are towns, not gauges.** The builder keeps a position only if it falls inside the district the station belongs to. Eight stations have no position yet; coordinators can add one in the app. **Import bulletin** on the River gauges list reads a newer Irrigation Department bulletin: latest levels, the state each gauge is in, and any revised thresholds.
- **Disaster history is recorded events, not hazard zoning.** Records thin out after 2019.
- **Lifelines and homes are what volunteers mapped.** Power substations are not in this export.
- **Population is district level**, projected from the 2012 census. The list also takes DS rows (by CSV import) from the 2024 census divisional tables; the census website could not be reached when this seed was built.
- **No personal data.** Contacts hold hotlines and desks, never personal phone numbers. Lists hold no people, NIC numbers or shelter registers.

## Refreshing

1. Run `python scripts/build_reference_seed.py`. It downloads the hospital and population files again and keeps gauge positions that are already known.
2. Review the changes before using them.
3. To load a rebuilt list into a deployment that has already started, run `python scripts/upload_reference_seed.py --bucket NAME`. It is a dry run unless you add `--apply`. It uploads only lists the bucket lacks. `--replace NAME` replaces a list only if nobody has edited it there yet. To change an edited list, export it as CSV, merge, and import it in the app.

## Public-release rights gate

Attribution-only provenance is not itself a redistribution license. Confirm the exact terms for the gauge bulletin, DesInventar history, Sphere-derived summaries and other upstream datasets before public release; see [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md). This private preparation copy does not claim those checks are complete. Refresh/upload utilities must target your own resources and are not run by default CI.
