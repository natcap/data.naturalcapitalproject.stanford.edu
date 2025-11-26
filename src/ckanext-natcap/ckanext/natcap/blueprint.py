from flask import Blueprint, Response, abort, send_file, redirect
from ckan.plugins import toolkit
import requests, os
from io import BytesIO
import zipfile


# blueprint.py
import re
import urllib.request
import urllib.error

from ckan.common import config

import logging
log = logging.getLogger(__name__)

bp = Blueprint("natcap", __name__)

def _resource_show(res_id):
    ctx = {"for_view": True}
    try:
        return toolkit.get_action("resource_show")(ctx, {"id": res_id})
    except toolkit.ObjectNotFound:
        abort(404)

# def _download_bytes(url: str) -> bytes:
#     r = requests.get(url, timeout=30)
#     r.raise_for_status()
#     return r.content
def _download_bytes(url: str) -> bytes:
    safe_url = _sanitize_local_resource_url(url)
    r = requests.get(safe_url, timeout=30)
    r.raise_for_status()
    return r.content



# @bp.route("/metadata/<resource_id>/preview")
# def metadata_preview(resource_id):
#     res = _resource_show(resource_id)
#     url = res.get("url")
#     if not url:
#         abort(404)
#     body = _download_bytes(url)
#     # Plain text page, new-window friendly
#     return Response(body, mimetype="text/plain; charset=utf-8")
def _sanitize_local_resource_url(resource_url: str) -> str:
    """
    Normalize resource URLs so they are reachable from inside the CKAN container.

    - If URL is like https://localhost:8443/... (dev via nginx),
      rewrite to http://localhost:5000/... (CKAN's internal port).
    """
    if not resource_url:
        return resource_url

    if re.match(r'^https?://localhost', resource_url):
        # Strip scheme+host+port, keep the path
        # eg. https://localhost:8443/dataset/... -> dataset/...
        path_only = re.sub(r'^https?://localhost:[0-9]+/', '', resource_url)

        # Use CKAN internal site URL or fallback
        ckan_site_url = config.get('ckan.site_url', 'http://localhost:5000')
        return f"{ckan_site_url.rstrip('/')}/{path_only.lstrip('/')}"
    return resource_url

@bp.route('/metadata/<resource_id>/preview')
def metadata_preview(resource_id):
    context = {'ignore_auth': True, 'user': toolkit.g.user or toolkit.g.author}
    res_dict = toolkit.get_action('resource_show')(context, {'id': resource_id})

    raw_url = res_dict.get('url')
    resource_url = _sanitize_local_resource_url(raw_url)

    try:
        with urllib.request.urlopen(resource_url) as resp:
            # assume utf-8 text for YAML
            text = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        # log and return something meaningful
        log.exception(
            f"Failed to fetch metadata from {resource_url}: {e}")
        return Response(
            f"Could not load metadata from {resource_url}\n\n{e}",
            mimetype='text/plain',
            status=502,
        )

    return Response(text, mimetype='text/plain; charset=utf-8')


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




@bp.route('/metadata/<resource_id>/download')
def metadata_download(resource_id):
    context = {'ignore_auth': True, 'user': toolkit.g.user or toolkit.g.author}
    res_dict = toolkit.get_action('resource_show')(context, {'id': resource_id})

    raw_url = res_dict.get('url')
    resource_url = _sanitize_local_resource_url(raw_url)

    # simplest: redirect the browser to the actual file URL
    return redirect(resource_url)
