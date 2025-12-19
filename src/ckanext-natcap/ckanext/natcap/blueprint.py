from flask import Blueprint, Response, abort, send_file, request
from ckan.plugins import toolkit
import requests, os
from io import BytesIO
import tarfile
import zipfile
import re
from urllib.parse import urlparse

import logging
log = logging.getLogger(__name__)

bp = Blueprint("natcap", __name__)


SHAPEFILE_PART_EXTS = [
    ".shp", ".dbf", ".shx", ".prj", ".cpg", ".qix", ".sbn", ".sbx"
]


def _replace_basename(url: str, new_name: str) -> str:
    """Replace only the filename portion of a URL."""
    base = url.rsplit("/", 1)[0]
    return f"{base}/{new_name}"


def _try_download(url: str):
    try:
        return _download_bytes(url)
    except Exception as e:
        log.info(f"Skipping missing sidecar: {url} ({e})")
        return None


def _resource_show(res_id):
    ctx = {"for_view": True}
    try:
        return toolkit.get_action("resource_show")(ctx, {"id": res_id})
    except toolkit.ObjectNotFound:
        abort(404)


def _download_bytes(url: str) -> bytes:
    safe_url = _sanitize_local_resource_url(url)
    r = requests.get(safe_url, timeout=30)
    r.raise_for_status()
    return r.content


def _sanitize_local_resource_url(resource_url: str) -> str:
    """
    Normalize resource URLs so they are reachable from inside the CKAN container.

    - If URL is like https://localhost:8443/... (dev via nginx),
      rewrite to http://localhost:5000/... (CKAN's internal port).
    """
    if not resource_url:
        return resource_url

    # Match http(s)://localhost[:port]/path...
    m = re.match(r'^https?://localhost(?::\d+)?/(.*)$', resource_url)
    if m:
        path_only = m.group(1)  # e.g. 'dataset/...'
        # IMPORTANT: use the internal CKAN address, *not* ckan.site_url
        internal_base = 'http://localhost:5000'
        # If you prefer to hit the service name instead:
        # internal_base = 'http://ckan:5000'
        return f"{internal_base.rstrip('/')}/{path_only.lstrip('/')}"

    # All other URLs (e.g. GCS, s3, real domains) pass through unchanged
    return resource_url


def _filename_from_resource(res: dict) -> str:
    # Prefer CKAN resource.name, fallback to URL basename
    n = (res.get("name") or "").strip()
    if n:
        return n
    url = res.get("url", "") or ""
    # handle querystrings cleanly
    path = urlparse(url).path
    return os.path.basename(path)


def _collect_shapefile_parts(pkg_resources: list[dict], shp_filename: str) -> list[dict]:
    """
    Given the main .shp filename, return all resources in the package that look like
    sibling shapefile parts with the same stem.
    """
    stem = os.path.splitext(shp_filename)[0]

    parts = []
    for r in pkg_resources:
        fname = _filename_from_resource(r)
        f_low = fname.lower()

        # exact sibling match: foo.<ext>
        # special-case ".shp.xml" which is a double extension
        if f_low == f"{stem}.shp.xml":
            parts.append(r)
            continue

        base, ext = os.path.splitext(f_low)
        if base == stem and ext in SHAPEFILE_PART_EXTS:
            parts.append(r)

    # Ensure the .shp is first (nice UX)
    def sort_key(r):
        f = _filename_from_resource(r).lower()
        return (0 if f.endswith(".shp") else 1, f)
    return sorted(parts, key=sort_key)


@bp.route("/bundle/<resource_id>")
def bundle_zip(resource_id):
    """ZIP = data file + its '<filename>.yml' if present."""
    data_res = _resource_show(resource_id)

    # Find attached metadata by filename inside the same package
    pkg = toolkit.get_action("package_show")({}, {"id": data_res["package_id"]})
    resources = pkg.get("resources", []) or []

    name = data_res.get("name") or os.path.basename(data_res.get("url",""))
    candidates = [f"{name}.yml", f"{name}.yaml"]

    meta_res = None
    for r in resources:
        rname = r.get("name") or os.path.basename(r.get("url","")) or ""
        if rname in candidates:
            meta_res = r
            break

    zbuf = BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        # data
        data_bytes = _download_bytes(data_res["url"])
        data_name = name
        z.writestr(data_name, data_bytes)

        # metadata
        if meta_res and meta_res.get("url"):
            yname = meta_res.get("name") or "metadata.yml"
            z.writestr(yname, _download_bytes(meta_res["url"]))

    zbuf.seek(0)
    bundle_name = f"{name}_with_metadata.zip"
    return send_file(zbuf, as_attachment=True, download_name=bundle_name, mimetype="application/zip")


@bp.route("/bundle-tar/<resource_id>")
def bundle_tar(resource_id):
    """Return a .tar bundle = data file + its '<filename>.yml/.yaml' if present."""
    data_res = _resource_show(resource_id)

    # Fetch package and resources
    pkg = toolkit.get_action("package_show")({}, {"id": data_res["package_id"]})
    resources = pkg.get("resources", []) or []

    # Base name to use for matching and for the data file
    name = data_res.get("name") or os.path.basename(data_res.get("url", "")) or "resource"

    # Look for attached YAML (must match dataset filename)
    candidates = [f"{name}.yml", f"{name}.yaml"]
    meta_res = None
    for r in resources:
        rname = r.get("name") or os.path.basename(r.get("url", "")) or ""
        if rname in candidates:
            meta_res = r
            break

    # Decide what data files to include
    data_resources_to_add = [data_res]
    if name.lower().endswith(".shp"):
        # swap to include all sibling parts in the package
        data_resources_to_add = _collect_shapefile_parts(resources, name)

    # Build the .tar in memory
    tbuf = BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w") as tar:

        # Add data (or shapefile parts)
        for r in data_resources_to_add:
            fname = _filename_from_resource(r) or "file"
            r_url = r.get("url")
            if not r_url:
                continue
            data_bytes = _download_bytes(r_url)
            info = tarfile.TarInfo(name=fname)
            info.size = len(data_bytes)
            tar.addfile(info, BytesIO(data_bytes))

        # 2) Add the YAML metadata if found
        if meta_res and meta_res.get("url"):
            y_url = meta_res["url"]
            y_name = meta_res.get("name") or f"{name}.yml"
            y_bytes = _download_bytes(y_url)
            y_info = tarfile.TarInfo(name=y_name)
            y_info.size = len(y_bytes)
            tar.addfile(y_info, BytesIO(y_bytes))

    tbuf.seek(0)
    bundle_name = f"{name.replace('.zip', '')}_with_metadata.tar"
    return send_file(
        tbuf,
        as_attachment=True,
        download_name=bundle_name,
        mimetype="application/x-tar",
    )


@bp.route("/bundle-source-tar")
def bundle_source_tar():
    """
    Bundle a zip-expanded source and its YAML into a .tar
    Supports shapefiles by including all sidecar parts.
    """
    source_url = request.args.get("source_url", "")
    source_name = (
        request.args.get("source_name")
        or os.path.basename(source_url)
        or "source"
    )

    meta_url = request.args.get("meta_url")
    meta_name = request.args.get("meta_name") or f"{source_name}.yml"

    tbuf = BytesIO()

    try:
        with tarfile.open(fileobj=tbuf, mode="w") as tar:

            # ---- SHAPEFILE CASE ----
            if source_name.lower().endswith(".shp"):
                shp_dir = os.path.dirname(source_name)
                shp_base = os.path.splitext(os.path.basename(source_name))[0]

                for ext in SHAPEFILE_PART_EXTS:
                    part_name = f"{shp_base}{ext}"
                    tar_path = (
                        os.path.join(shp_dir, part_name)
                        if shp_dir else part_name
                    )

                    part_url = _replace_basename(source_url, part_name)
                    part_bytes = _try_download(part_url)
                    if part_bytes is None:
                        continue

                    info = tarfile.TarInfo(name=tar_path)
                    info.size = len(part_bytes)
                    tar.addfile(info, BytesIO(part_bytes))

            # ---- NON-SHAPEFILE CASE ----
            else:
                source_bytes = _download_bytes(source_url)
                info = tarfile.TarInfo(name=source_name)
                info.size = len(source_bytes)
                tar.addfile(info, BytesIO(source_bytes))

            # ---- YAML METADATA ----
            if meta_url:
                meta_bytes = _try_download(meta_url)
                if meta_bytes is not None:
                    info = tarfile.TarInfo(name=meta_name)
                    info.size = len(meta_bytes)
                    tar.addfile(info, BytesIO(meta_bytes))

    except Exception as e:
        log.exception("Failed to build source tar")
        return Response(
            f"Failed to build tar archive.\n\n{e}",
            status=502,
            mimetype="text/plain",
        )

    tbuf.seek(0)
    bundle_name = f"{os.path.basename(source_name.replace('.', '_'))}_with_metadata.tar"
    return send_file(
        tbuf,
        as_attachment=True,
        download_name=bundle_name,
        mimetype="application/x-tar",
    )
