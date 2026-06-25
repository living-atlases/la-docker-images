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
| `sds.<domain>` home (the public site) | static (Vite/React) | **active, generic** | a generic static nginx image (see Plan) |

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

1. **One generic static `sds` nginx image** here in `la-docker-images`
   (productionise `build/temp_sensitive/ala-sds-static-home/`): brandable via
   build/deploy env, no portal data baked in.
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
