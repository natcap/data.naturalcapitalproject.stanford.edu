from flask import Blueprint, Response, abort, send_file, redirect, request, stream_with_context
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

import tempfile
import threading
from ckan.common import config

DEFAULT_TIMEOUT = (10, 600)  # (connect, read) seconds

def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    return os.path.basename(path) or "file"

def _sanitize_local_resource_url(resource_url: str) -> str:
    if not resource_url:
        return resource_url
    m = re.match(r'^https?://localhost(?::\d+)?/(.*)$', resource_url)
    if m:
        path_only = m.group(1)
        internal_base = 'http://localhost:5000'
        return f"{internal_base.rstrip('/')}/{path_only.lstrip('/')}"
    return resource_url

def _head_content_length(url: str) -> int | None:
    """Return Content-Length if available and parseable."""
    try:
        r = requests.head(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        cl = r.headers.get("Content-Length")
        if cl is None:
            return None
        return int(cl)
    except Exception:
        return None

def _stream_get(url: str) -> requests.Response:
    """GET streaming response (caller must close)."""
    r = requests.get(url, stream=True, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    # ensure transparent decompression doesn’t break sizes
    r.raw.decode_content = False
    return r

def _add_url_as_tar_member(tar: tarfile.TarFile, url: str, arcname: str):
    """
    Add a URL to a tar stream.
    Uses Content-Length for true streaming when possible;
    otherwise spools to disk for that member.
    """
    url = _sanitize_local_resource_url(url)

    size = _head_content_length(url)
    if size is not None:
        # True streaming: tar reads directly from response.raw
        r = _stream_get(url)
        try:
            ti = tarfile.TarInfo(name=arcname)
            ti.size = size
            tar.addfile(ti, fileobj=r.raw)
        finally:
            r.close()
        return

    # Fallback: spool to disk to learn size
    with tempfile.NamedTemporaryFile(mode="w+b", delete=True) as tmp:
        r = _stream_get(url)
        try:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    tmp.write(chunk)
        finally:
            r.close()
        tmp.flush()
        tmp.seek(0, os.SEEK_END)
        size = tmp.tell()
        tmp.seek(0)

        ti = tarfile.TarInfo(name=arcname)
        ti.size = size
        tar.addfile(ti, fileobj=tmp)



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
    """Return a .tar bundle = data file + metadata yaml if present."""
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
        rname = _filename_from_resource(r)
        if rname in candidates:
            meta_res = r
            break

    # Decide what data files to include
    data_resources_to_add = [data_res]
    if name.lower().endswith(".shp"):
        # swap to include all sibling parts in the package
        data_resources_to_add = _collect_shapefile_parts(resources, name)

    meta_res_url = meta_res.get("url") if meta_res else None
    if not name.lower().endswith(".shp") and not meta_res_url:
        # Direct-download fallback for non-shp single files w/out metadata
        if not data_res.get("url"):
            abort(400, "Missing resource url")
        # return redirect(_sanitize_local_resource_url(data_res.get("url")))
        try:
            data = _download_bytes(data_res.get("url"))
        except Exception as e:
            log.exception("Direct download failed")
            abort(502)

        filename = _filename_from_resource(data_res) or "download"

        return send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=filename,
            mimetype="application/octet-stream",
        )

    out_name = f"{os.path.basename(name.replace('.', '_'))}_with_metadata.tar"
    headers = {"Content-Disposition": f'attachment; filename="{out_name}"'}

    r_fd, w_fd = os.pipe()

    def writer():
        try:
            with os.fdopen(w_fd, "wb") as w:
                with tarfile.open(fileobj=w, mode="w|") as tar:
                    # ---- DATA FILES ----
                    for r in data_resources_to_add:
                        fname = _filename_from_resource(r) or "file"
                        r_url = r.get("url")
                        if not r_url:
                            continue
                        _add_url_as_tar_member(tar, r_url, fname)

                    # ---- YAML METADATA ----
                    if meta_res and meta_res.get("url"):
                        _add_url_as_tar_member(
                            tar, meta_res["url"],
                            _filename_from_resource(meta_res) or f"{name}.yml",)
        except Exception:
            pass
        finally:
            try:
                os.close(w_fd)
            except Exception:
                pass

    threading.Thread(target=writer, daemon=True).start()
    def generate():
        with os.fdopen(r_fd, "rb") as r:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-tar",
        headers=headers,
        direct_passthrough=True,
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
        or _filename_from_url(source_url)
        or "source"
    )

    if not source_url:
        abort(400, "Missing source_url")

    meta_url = request.args.get("meta_url")
    meta_name = request.args.get("meta_name") or f"{source_name}.yml"

    if not meta_url and not source_name.lower().endswith(".shp"):
        try:
            data = _download_bytes(source_url)
        except Exception as e:
            log.exception("Direct download failed")
            abort(502)

        filename = os.path.basename(source_name) or "download"

        return send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=filename,
            mimetype="application/octet-stream",
        )

    out_name = f"{os.path.basename(source_name.replace('.', '_'))}_with_metadata.tar"
    headers = {
        "Content-Disposition": f'attachment; filename="{out_name}"'
    }

    r_fd, w_fd = os.pipe()

    def writer():
        try:
            with os.fdopen(w_fd, "wb") as w:
                with tarfile.open(fileobj=w, mode="w|") as tar:
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
                            # try adding sidecars; if 404/403 skip quietly
                            try:
                                _add_url_as_tar_member(tar, part_url, tar_path)
                            except requests.HTTPError as e:
                                code = getattr(e.response, "status_code", None)
                                if code in (403, 404):
                                    continue
                    else: # non-shp
                        _add_url_as_tar_member(tar, source_url, source_name)

                    # ---- YAML METADATA ----
                    if meta_url:
                        try:
                            _add_url_as_tar_member(tar, meta_url, meta_name)
                        except requests.HTTPError as e:
                            code = getattr(e.response, "status_code", None)
                            if code not in (403, 404):
                                raise
        except Exception:
            pass # close pipe if error; user will see incomplete download
        finally:
            try:
                os.close(w_fd)
            except Exception:
                pass

    threading.Thread(target=writer, daemon=True).start()

    def generate():
        with os.fdopen(r_fd, "rb") as r:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-tar",
        headers=headers,
        direct_passthrough=True,
    )

