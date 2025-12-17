from flask import Blueprint, Response, abort, send_file, redirect
from ckan.plugins import toolkit
import requests, os
from io import BytesIO
import tarfile
import zipfile
import re

import logging
log = logging.getLogger(__name__)

bp = Blueprint("natcap", __name__)


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

    # Build the .tar in memory
    tbuf = BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w") as tar:

        # 1) Add the data file
        data_url = data_res["url"]
        data_bytes = _download_bytes(data_url)
        data_info = tarfile.TarInfo(name=name)
        data_info.size = len(data_bytes)
        tar.addfile(data_info, BytesIO(data_bytes))

        # 2) Add the YAML metadata if found
        if meta_res and meta_res.get("url"):
            y_url = meta_res["url"]
            y_name = meta_res.get("name") or f"{name}.yml"
            y_bytes = _download_bytes(y_url)
            y_info = tarfile.TarInfo(name=y_name)
            y_info.size = len(y_bytes)
            tar.addfile(y_info, BytesIO(y_bytes))

    tbuf.seek(0)
    bundle_name = f"{name.replace(".zip", '')}_with_metadata.tar"
    return send_file(
        tbuf,
        as_attachment=True,
        download_name=bundle_name,
        mimetype="application/x-tar",
    )
