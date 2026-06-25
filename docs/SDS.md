# SDS (Sensitive Data Service) — build & image notes

> Cross-repo record. Investigated 2026-06-24 from `la-docker-compose`
> (commit `97609cb`). Captured here so the build side has the same context.
> SDS deployment work is **deferred to its own session** — this is a record, not a task.

## TL;DR

There are **two different things** historically called "SDS", and only one is
still a deployable image:

| Thing | Repo | Status | What to build |
|---|---|---|---|
| `sds-webapp2` (Grails webapp) | `AtlasOfLivingAustralia/sds` | **DEPRECATED** | nothing — see below |
| `ala-sensitive-data-server` (Dropwizard service) | `AtlasOfLivingAustralia/ala-sensitive-data-service` | **active** | already built here (`services-definition.yml:175`) |
| `sds.<domain>` home (the public site) | static (Vite/React) | **active, generic** | ✅ `sds-static-home` image — `services/sds-static-home/Dockerfile` |

## 1. `sds-webapp2` is obsolete (`services-definition.yml:137`)

The `sds-webapp2` entry builds the old Grails webapp from
`github.com/AtlasOfLivingAustralia/sds` with `build_tool: gradle`,
`artifacts: sds-webapp2`. That repo is **no longer a deployable webapp**:

- It is now a **Maven library** (`SensitiveSpeciesXmlBuilder`) that *generates*
  the SDS XML files. Its Travis/CI builds a JAR, **not** a `sds-webapp2` WAR.
- XML generation moved to an **Airflow job → S3/CloudFront**.
- So `build_tool: gradle` + `artifacts: sds-webapp2` cannot succeed against the
  current `master`; the entry is a historical artifact.

**Do not "fix" it by repointing the build tool** — there is no webapp to build.
Leave the entry (and its `# DEPRECATED` comment) until the static-home image
below replaces the deployment need, then remove it.

## 2. Modern `sds.<domain>` = static home + S3-hosted XML

The public `sds.ala.org.au` / `sds.l-a.site` is now:

- a **static home page** (the Vite/React app already present in this repo at
  `build/temp_sensitive/ala-sds-static-home/`), plus
- **XML data files served as static assets** from S3/CloudFront:
  `sensitive-species-data.xml`, `sensitivity-categories.xml`,
  `sensitivity-zones.xml` (and `layers.json`).

## 3. Who consumes the XML

`ala-sensitive-data-server` (the Dropwizard service we DO build,
`services-definition.yml:175`; note `app_args: server
/data/ala-sensitive-data-service/config/config.yml`) **downloads those XML at
deploy time** from `{{ sds_url }}` (ala-install role
`sensitive-data-service/tasks/docker-tasks.yml`), with a `rescue:` fallback to
`https://sds.ala.org.au`. If the portal's own `sds` endpoint 404s, it silently
falls back to ALA-AU's data — which is the root of the runtime NPE below.

## 4. Known runtime issue (DATA, not image)

With the deprecated app / ALA-AU fallback data we hit
`NullPointerException` in `SensitivityCategoryFactory.getCategory`: the
species-data references categories by **name** (`Endangered`, `Vulnerable`,
`Critically Endangered`) while `sensitivity-categories.xml` keys them by **id**
(`EN`, `VU`, `CR`). This is a **per-portal data** problem, fixed in the data
layer, not in the image.

## Plan (for the dedicated SDS session)

Aligned with the LA reusability guidance ("data that differs per organisation =
config at deploy, not a rebuild"):

1. **✅ DONE (this repo): generic `sds-static-home` nginx image** —
   `services/sds-static-home/Dockerfile` + `services-definition.yml`. Builds gen-2
   `ala-sds-static-home` from `ala-sensitive-data-service` master; endpoints are set
   **per portal at deploy** via env (`SDS_WS_URL`, `SDS_XML_URL`, `SDS_SWAGGER_URL`,
   `SDS_LISTS_URL`, `SDS_HELP_URL`) using placeholder tokens substituted at container
   start — **one image, no rebuild**, relative defaults. No portal data baked in.
   (Branding/i18n are still ALA — see *Reusability test* below.)
2. **Per-portal SDS data provisioning at deploy** in `la-docker-compose`
   (mount the portal's `sensitive-species-data.xml` / `sensitivity-*.xml`),
   so `ala-sensitive-data-server` downloads from the *local* `sds` endpoint
   instead of falling back to ALA-AU.
3. Then drop the `sds-webapp2` entry from `services-definition.yml`.

## la-docker-compose side (already done / separate)

- Mount target fix `server`→`service` for `ala-sensitive-data-server`
  (la-docker-compose `97609cb`): the image is named `-server` but the app reads
  config at `/data/ala-sensitive-data-service/config/` — confirmed by
  `app_args` above. This made the container go from `Exited(1)` to `Up`.
- That fix is independent of the data/NPE work described here.

## Reusability test (2026-06-25)

Scored against *"Reusability guidelines for ALA next-gen components"* (bar: **one
published artifact configured per LA portal at deploy time**; ground rule: **don't
disrupt ALA's own workflow**).

| Concern | Backend `ala-sensitive-data-server` | SDS home/UI |
|---|---|---|
| Config overridable at runtime (no rebuild) | ✅ `config.yml` mounted | ✅ *after this change* (was ✗: Vite baked `VITE_APP_*` into `dist`) |
| Public versioned artifact | ✅ image published | ✅ *after this change* (`sds-static-home`; was ✗: S3/CloudFront only) |
| External/overridable branding | n/a | ❌ hard-links `ala.org.au` BS3 + ALA header/footer |
| i18n loadable at runtime | n/a | ❌ English-only (gen-2) |
| Portable per-portal data bootstrap | ⚠️ `sds_url` falls back to ALA-AU | ❌ XML is ALA-AU (Airflow→S3) |

**This repo flips Config + Artifacts to ✅** for the home page. Branding, i18n and
per-portal data remain open — mapped below. All changes are LA-side build only; ALA's
source and S3/CloudFront path are untouched.

## Gen-3 successor: `sds-ui` (atlas-index)

`AtlasOfLivingAustralia/atlas-index` is the Next-gen monorepo; **`sds-ui` `v1.0.0`
(2026-06-25)** is the modern SDS UI — Vite 7 / React 19 + `react-intl` (i18n), bundled
Bootstrap 5 (no `ala.org.au` CDN). Deferred for now: it's source-only (no artifact),
**requires the whole monorepo** (`@ala/common-ui` workspace) to build, and targets
next-gen `atlas-index` APIs rather than the classic `ala-sensitive-data-service`
v1.1.1 LA portals run today. It is the path to close the **branding** and **i18n**
gaps once a portal adopts atlas-index.

## Remaining work (follow-ups)

- **Per-portal data** (`la-docker-compose`, SDS.md step 2): serve the portal's own
  `sensitive-species-data.xml` / `sensitivity-*.xml` at the routed endpoint; drop the
  silent ALA-AU fallback; keep the source S3-compatible/local.
- **Branding / i18n**: via gen-3 `sds-ui` (above).
- **Drop `sds-webapp2`** from `services-definition.yml` once compose switches (step 3).
