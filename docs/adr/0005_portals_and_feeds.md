# ADR 0005: Portals own listing identity, feeds own schedules

## Status
Accepted (migration `002_listing_feeds`)

## Context
Every crawler config (`site_id`) was its own identity space: `listing_key = <site_id>_<id>` and
`listings.source = site_id`. Several configs crawl the *same* portal with different filters:
`mogi` / `mogi_apartment`, `homedy` / `homedy_apartment`, `nhatot` / `nhatot_hn`. An ad shown in
both feeds became two listings, two price histories and two rows in the ward price averages.
Lifecycle could not simply move to the portal either: a `mogi_apartment` full sweep only sees
apartments, so it would count every mogi house as "missing".

## Decision
- A **portal** is the owner of a listing-ID space. `CrawlerConfig.portal` (default: `site_id`)
  names it; `listing_key = <portal>_<id>`, `listings.source = portal`, advertiser IDs use the portal.
- A **feed** is a crawler config: it keeps its schedule, `crawl_runs` and metrics.
- `listing_feeds(listing_key, site_id, first_seen_at, last_seen_at)` records which feeds have
  seen a listing. A feed's full sweep counts a listing as missing only if **that feed** has seen
  it before and **no feed of the portal** has seen it since the sweep started.
- `portal` must name a configured site that is not itself a feed of another portal
  (contract test). `cafeland_project` also maps to `cafeland`: its source page
  (`/du-an-dang-ban/`, after `/du-an/` was removed) lists ads inside projects with cafeland
  listing IDs. It had never ingested anything, so migration 002 does not need to re-key it.
- Migration 002 re-keys existing rows (`mogi_apartment_123` → `mogi_123`). When both keys exist,
  the rows are merged into the portal row: the observed window is widened, the newer status wins,
  and feed memberships are kept.

## Consequences
- One ad is one listing whatever the number of feeds that list it.
- A listing in two feeds can collect missing counts from both feeds' sweeps, so it can expire
  after fewer calendar days than one with a single feed. That is acceptable: it really is
  missing from both feeds.
- Migration 002 is not reversible for merged rows (the downgrade keeps portal keys).
- The Bronze layer (Mongo `raw_listings`) keeps old keys for already-ingested raw events.
