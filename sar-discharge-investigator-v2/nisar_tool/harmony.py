"""Prepare explicit NISAR Harmony requests for the user's signed-in browser.

Plan construction and HTML writing are offline. The optional preparation
helper reads only public CMR metadata and Harmony capabilities through the
supplied bounded transport. No function submits a job, opens a browser, or
reads credentials. A Python process does not inherit an Earthdata browser
session; a generated link submits a request only when the user opens it.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Mapping, Sequence
from numbers import Real
from pathlib import Path
from urllib.parse import urlencode

from .errors import ApiError


HARMONY_ROOT = "https://harmony.earthdata.nasa.gov"
DOCUMENTATION_URLS = (
    "https://nisar-docs.asf.alaska.edu/harmony/",
    "https://harmony.earthdata.nasa.gov/docs",
)

# These full paths were advertised by the respective live ASF collection
# capabilities, and the repeated-query browser route produced successful
# GeoTIFF subset jobs on 2026-11-14. This does not establish their presence in
# every granule, frequency, polarization mode, or future collection version.
DEFAULT_VARIABLES = {
    "GCOV": (
        "/science/LSAR/GCOV/grids/frequencyA/HHHH",
        "/science/LSAR/GCOV/grids/frequencyA/HVHV",
        "/science/LSAR/GCOV/grids/frequencyA/mask",
    ),
    "GUNW": (
        "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/unwrappedPhase",
        "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/coherenceMagnitude",
        "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/connectedComponents",
        "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/mask",
    ),
}

_PRODUCTS = frozenset({"GCOV", "GUNW", "GOFF", "SME2"})
_COLLECTION = re.compile(r"C[1-9][0-9]*-([A-Z][A-Z0-9_]*)\Z")
_GRANULE = re.compile(r"G[1-9][0-9]*-([A-Z][A-Z0-9_]*)\Z")
_VARIABLE_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PRODUCT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,511}\Z")


def _concept_id(value: str, pattern: re.Pattern, label: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a CMR concept ID string.")
    value = value.strip()
    match = pattern.fullmatch(value)
    if not match:
        raise ValueError(f"Invalid {label}: use a CMR concept ID, not a URL or product name.")
    return value, match.group(1)


def _bbox(value: Sequence[Real]) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 4:
        raise ValueError("bbox must contain [west, south, east, north].")
    if any(isinstance(v, bool) or not isinstance(v, Real) for v in value):
        raise ValueError("bbox coordinates must be finite numbers.")
    bounds = [float(v) for v in value]
    if not all(math.isfinite(v) for v in bounds):
        raise ValueError("bbox coordinates must be finite numbers.")
    west, south, east, north = bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox must have west < east and south < north within longitude/latitude bounds.")
    return bounds


def _variables(product: str, variables: Sequence[str] | None) -> list[str]:
    if variables is None:
        if product not in DEFAULT_VARIABLES:
            raise ValueError(
                f"{product} requires explicit variable paths verified against its collection capabilities."
            )
        return list(DEFAULT_VARIABLES[product])
    if isinstance(variables, (str, bytes)) or not isinstance(variables, Sequence) or not variables:
        raise ValueError("variables must be a non-empty list of explicit full dataset paths.")
    prefix = f"/science/LSAR/{product}/grids/"
    result = []
    for variable in variables:
        if not isinstance(variable, str) or not variable.startswith(prefix):
            raise ValueError(f"Each variable must be a full path below {prefix}.")
        suffix = variable[len(prefix):]
        if not suffix or any(not _VARIABLE_SEGMENT.fullmatch(part) for part in suffix.split("/")):
            raise ValueError("Variable paths may contain only explicit dataset-name segments; no URLs or traversal.")
        if variable not in result:
            result.append(variable)
    return result


def build_subset_plan(
    product: str,
    collection_id: str,
    granule_id: str,
    bbox: list[float],
    variables: list[str] | None = None,
) -> dict:
    """Build an unsubmitted, one-granule scientific GeoTIFF subset request.

    ``bbox`` uses WGS84 degrees in west/south/east/north order and must not
    cross the antimeridian. GCOV defaults to H-transmit frequency-A channels;
    GUNW defaults to the HH unwrapped-interferogram layers. Other modes need
    caller-selected paths. GOFF and SME2 have no guessed defaults.

    IDs and paths are structurally validated only. Confirm collection/granule
    association, actual variable presence, access, and available service
    capabilities separately before submitting the link in the browser.
    """
    if not isinstance(product, str) or product.strip().upper() not in _PRODUCTS:
        raise ValueError("product must be GCOV, GUNW, GOFF, or SME2.")
    product = product.strip().upper()
    collection_id, collection_provider = _concept_id(collection_id, _COLLECTION, "collection_id")
    granule_id, granule_provider = _concept_id(granule_id, _GRANULE, "granule_id")
    if collection_provider != granule_provider:
        raise ValueError("Collection and granule concept IDs must use the same CMR provider.")
    bounds = _bbox(bbox)
    selected = _variables(product, variables)
    west, south, east, north = bounds
    endpoint = (
        f"{HARMONY_ROOT}/{collection_id}/ogc-api-coverages/1.0.0/"
        "collections/parameter_vars/coverage/rangeset"
    )
    # Repeated full-path parameters are intentional. Comma-joined concept IDs
    # were rejected by the live browser route; repeated paths were successful.
    params = [
        ("granuleId", granule_id),
        ("subset", f"lat({south}:{north})"),
        ("subset", f"lon({west}:{east})"),
        ("format", "image/tiff"),
        ("forceAsync", "true"),
        ("maxResults", "1"),
        ("ignoreErrors", "false"),
        *(("variable", path) for path in selected),
    ]
    return {
        "product": product,
        "collection": collection_id,
        "collection_id": collection_id,
        "granule": granule_id,
        "granule_id": granule_id,
        "bbox": bounds,
        "bbox_crs": "EPSG:4326",
        "variables": selected,
        "browser_request_url": f"{endpoint}?{urlencode(params)}",
        "auth_required": True,
        "authentication_mode": "existing_browser_session",
        "api_token_supported_but_not_enabled": True,
        "status": "planned_not_submitted",
        "scientific": False,
        "downloads": 0,
        "capabilities_verified": False,
        "size_preflight_required": True,
        "notes": [
            "This is a request plan, not a submitted job, scientific data, or proof of download permission.",
            "Open the handoff in the same browser profile used for Earthdata Login; a Python process does not inherit that browser session.",
            "Sign in or approve provider terms on NASA's own pages when required. No passwords, tokens, cookies or .netrc files are read or written by this tool.",
            "Verify these paths against the collection capabilities and selected granule; defaults may not match a different polarization mode or product version.",
            "The link requests one granule, explicit variables and a geographic bounding-box subset as GeoTIFF. No reprojection or scaling is requested.",
            "Follow the returned job-status links. A successful processing job does not prove that scientific files have been downloaded or validated.",
            "Preflight generated asset sizes before downloading. Defer unknown-size results and respect the configured per-file and cumulative transfer budgets.",
            "Validate output CRS, dimensions, extent, masks, units and values after download; apply the exact projected AOI mask locally if required.",
        ],
        "documentation": list(DOCUMENTATION_URLS),
    }


def resolve_product_ids(transport, item: dict) -> dict:
    """Resolve a normalized ASF item to unique CMR collection/granule IDs.

    Only the public CMR metadata endpoint is read through the supplied bounded
    transport. An ASF product ID or its collection-name string is not a CMR
    granule concept ID. No science asset, browser state, or credentials are read.
    """
    if not isinstance(item, Mapping):
        raise ApiError("invalid_product", "A normalized ASF product item is required.")
    product_id = item.get("id")
    if not isinstance(product_id, str) or not _PRODUCT_ID.fullmatch(product_id):
        raise ApiError("invalid_product", "The item must contain a valid provider product ID.")
    params = {"provider": "ASF", "producer_granule_id": product_id, "page_size": 2}
    collection_hint = item.get("collection_concept_id")
    if collection_hint:
        try:
            collection_hint, provider = _concept_id(collection_hint, _COLLECTION, "collection_concept_id")
        except ValueError as error:
            raise ApiError("invalid_collection", str(error)) from None
        if provider != "ASF":
            raise ApiError("invalid_collection", "This resolver accepts public ASF collection IDs only.")
        params["collection_concept_id"] = collection_hint
    response = transport.get_json(
        "https://cmr.earthdata.nasa.gov/search/granules.json",
        params=params,
        cap=1_000_000,
        purpose="Resolve NISAR product IDs for a browser Harmony handoff",
    )
    feed = response.get("feed") if isinstance(response, Mapping) else None
    entries = feed.get("entry") if isinstance(feed, Mapping) else None
    if not isinstance(entries, list) or any(not isinstance(entry, Mapping) for entry in entries):
        raise ApiError("invalid_provider_response", "CMR did not return a valid granule result list.", 502)
    matching = [entry for entry in entries if entry.get("producer_granule_id") == product_id]
    if not matching:
        raise ApiError("granule_not_found", "No public ASF granule matched this exact product ID.", 404)
    if len(matching) != 1:
        raise ApiError(
            "ambiguous_granule", "Multiple CMR granules matched this product; select an explicit collection concept ID.", 409
        )
    entry = matching[0]
    try:
        collection, collection_provider = _concept_id(entry.get("collection_concept_id"), _COLLECTION, "collection_concept_id")
        granule, granule_provider = _concept_id(entry.get("id"), _GRANULE, "granule_concept_id")
    except ValueError:
        raise ApiError("invalid_provider_response", "CMR returned invalid collection or granule concept IDs.", 502) from None
    if collection_provider != "ASF" or granule_provider != "ASF" or collection_hint and collection != collection_hint:
        raise ApiError("invalid_provider_response", "CMR returned a granule outside the requested ASF collection.", 502)
    return {"collection_concept_id": collection, "granule_concept_id": granule}


def _advertised_default_variables(product: str, item: Mapping) -> list[str]:
    metadata = item.get("provider_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    polarizations = metadata.get("mainBandPolarization")
    if isinstance(polarizations, str):
        polarizations = [polarizations]
    if not isinstance(polarizations, (list, tuple)) or any(not isinstance(p, str) for p in polarizations):
        polarizations = []
    polarizations = list(dict.fromkeys(p.strip().upper() for p in polarizations))
    prefix = f"/science/LSAR/{product}/grids/frequencyA/"
    if product == "GCOV":
        diagonal = {"HH": "HHHH", "HV": "HVHV", "VV": "VVVV", "VH": "VHVH", "RH": "RHRH", "RV": "RVRV"}
        if polarizations and all(p in diagonal for p in polarizations):
            return [prefix + diagonal[p] for p in polarizations] + [prefix + "mask"]
    elif product == "GUNW":
        copolarized = [p for p in polarizations if p in {"HH", "VV"}]
        if copolarized:
            selected = copolarized[0]
            group = prefix + "unwrappedInterferogram/"
            return [group + selected + "/" + name for name in ("unwrappedPhase", "coherenceMagnitude", "connectedComponents")] + [group + "mask"]
    raise ApiError(
        "variables_required",
        "Supply explicit variable paths: this product's advertised main-band polarization does not establish safe defaults.",
        400,
    )


def prepare_subset_plan(transport, item: dict, bbox: list[float], variables: list[str] | None = None) -> dict:
    """Resolve a product and verify public capabilities without submitting it.

    For default variable selection, use the granule's advertised main-band
    polarization. GCOV selects diagonal power channels plus its mask; GUNW
    selects the first advertised HH/VV unwrapped group plus its mask. Missing
    mode metadata and GOFF/SME2 require explicit variables instead of guessing.
    Collection capability presence is not proof of variable presence or valid
    pixels inside the particular granule.
    """
    if not isinstance(item, Mapping):
        raise ApiError("invalid_product", "A normalized ASF product item is required.")
    product = item.get("product_type")
    if not isinstance(product, str) or product.upper() not in _PRODUCTS:
        raise ApiError("unsupported_subset_product", "Browser subset plans support GCOV, GUNW, GOFF, and SME2 only.")
    product = product.upper()
    try:
        bounds = _bbox(bbox)
        selected = _variables(product, variables) if variables is not None else _advertised_default_variables(product, item)
    except ValueError as error:
        raise ApiError("invalid_subset_parameters", str(error), 400) from None
    ids = resolve_product_ids(transport, item)
    plan = build_subset_plan(product, ids["collection_concept_id"], ids["granule_concept_id"], bounds, selected)
    capabilities = transport.get_json(
        f"{HARMONY_ROOT}/capabilities",
        params={"collectionId": ids["collection_concept_id"], "version": 3},
        cap=1_000_000,
        purpose="Verify public NISAR Harmony subset capabilities",
    )
    if not isinstance(capabilities, Mapping) or capabilities.get("conceptId") != ids["collection_concept_id"]:
        raise ApiError("invalid_provider_response", "Harmony returned capabilities for an unexpected collection.", 502)
    service_list = capabilities.get("services")
    variable_list = capabilities.get("variables")
    if not isinstance(service_list, list) or not isinstance(variable_list, list):
        raise ApiError("invalid_provider_response", "Harmony capabilities did not include valid service and variable lists.", 502)
    services = []
    for service in service_list:
        if not isinstance(service, Mapping):
            continue
        capability = service.get("capabilities")
        if not isinstance(capability, Mapping):
            continue
        subsetting = capability.get("subsetting")
        formats = capability.get("outputFormats")
        if (isinstance(subsetting, Mapping) and subsetting.get("bbox") is True and subsetting.get("variable") is True
                and isinstance(formats, list) and any(isinstance(f, Mapping) and f.get("mimeType") == "image/tiff" for f in formats)):
            services.append(service.get("name", "unnamed service"))
    if not services:
        raise ApiError("unsupported_harmony_subset", "This collection does not advertise one service supporting bounding-box and variable subsets to GeoTIFF.", 422)
    advertised = {v.get("name") for v in variable_list if isinstance(v, Mapping) and isinstance(v.get("name"), str)}
    missing = [v for v in selected if v not in advertised]
    if missing:
        raise ApiError("unsupported_variables", "Some selected variable paths are not advertised by this collection.", 422, {"variables": missing})
    plan.update({
        "source_product_id": item["id"],
        "capabilities_verified": True,
        "granule_variable_presence_verified": False,
        "variable_selection": "explicit caller selection" if variables is not None else "provider-advertised main-band polarization",
        "capability_verification": {
            "endpoint": f"{HARMONY_ROOT}/capabilities",
            "collection_id": ids["collection_concept_id"],
            "services": services,
            "output_format": "image/tiff",
        },
    })
    return plan


def write_handoff(plan: dict, output_path: Path) -> Path:
    """Write a local HTML handoff with one ordinary, explicit Harmony link.

    No JavaScript, automatic submission, browser launch, or credential access
    occurs. Rebuilding the URL prevents a modified plan from replacing the
    link with an arbitrary destination.
    """
    if not isinstance(plan, Mapping):
        raise ValueError("plan must be a subset plan mapping.")
    for short, explicit in (("collection", "collection_id"), ("granule", "granule_id")):
        if short in plan and explicit in plan and plan[short] != plan[explicit]:
            raise ValueError(f"Conflicting {short} concept IDs in the plan.")
    try:
        canonical = build_subset_plan(
            plan["product"],
            plan.get("collection_id", plan.get("collection")),
            plan.get("granule_id", plan.get("granule")),
            plan["bbox"],
            plan["variables"],
        )
    except KeyError as error:
        raise ValueError(f"Subset plan is missing {error.args[0]}.") from error
    if plan.get("browser_request_url", canonical["browser_request_url"]) != canonical["browser_request_url"]:
        raise ValueError("The handoff URL does not match the validated subset parameters.")
    output_path = Path(output_path)
    if output_path.suffix.lower() not in {".html", ".htm"}:
        raise ValueError("output_path must name an .html or .htm file.")
    esc = lambda value: html.escape(str(value), quote=True)
    variables_html = "".join(f"<li><code>{esc(v)}</code></li>" for v in canonical["variables"])
    notes_html = "".join(f"<li>{esc(note)}</li>" for note in canonical["notes"])
    references_html = " · ".join(
        f'<a href="{esc(url)}">{esc(label)}</a>'
        for url, label in zip(DOCUMENTATION_URLS, ("NISAR subset guide", "Harmony API documentation"))
    )
    page = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NISAR {esc(canonical['product'])} subset handoff</title>
<style>
body{{font:17px/1.6 system-ui,sans-serif;max-width:960px;margin:32px auto;padding:0 24px;color:#183047}}
code{{overflow-wrap:anywhere}}dt{{font-weight:650}}dd{{margin:0 0 12px}}li{{margin:8px 0}}
.request{{display:inline-block;padding:12px 18px;background:#075e8b;color:white;border-radius:6px;text-decoration:none}}
</style>
<h1>NISAR {esc(canonical['product'])} subset request</h1>
<p>Prepared locally. This request has not been submitted and no scientific files have been downloaded.</p>
<dl><dt>Collection</dt><dd><code>{esc(canonical['collection'])}</code></dd>
<dt>Granule</dt><dd><code>{esc(canonical['granule'])}</code></dd>
<dt>Bounding box: west, south, east, north (WGS84 degrees)</dt><dd>{esc(canonical['bbox'])}</dd></dl>
<h2>Selected variables</h2><ul>{variables_html}</ul>
<p>Open this file in the browser profile where you use Earthdata Login. Clicking below submits the
selected subset request to NASA Harmony; NASA may first ask you to sign in or approve provider terms.</p>
<p><a class="request" href="{esc(canonical['browser_request_url'])}">Open request in your Earthdata browser</a></p>
<h2>Processing and download checks</h2><ul>{notes_html}</ul>
<p>{references_html}</p>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")
    return output_path
