from flask import Blueprint, Response, abort, send_file, request, stream_with_context
from ckan.plugins import toolkit
import requests, os
from io import BytesIO
import logging
import re
import tarfile
import tempfile
import threading
from urllib.parse import urlparse

#new
import json
import time

log = logging.getLogger(__name__)
bp = Blueprint("natcap", __name__)


SHAPEFILE_PART_EXTS = [
    ".shp", ".dbf", ".shx", ".prj", ".cpg", ".qix", ".sbn", ".sbx"
]

DEFAULT_TIMEOUT = (600, 7200)  # (connect, read) seconds
LARGE_MEMBER_BYTES = 512 * 1024 * 1024  # 512MB
MAX_RETRIES = 3


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    return os.path.basename(path) or "file"


def _stream_get(url: str) -> requests.Response:
    """GET streaming response (caller must close)."""
    r = requests.get(url, stream=True, timeout=DEFAULT_TIMEOUT,
                     headers={"Accept-Encoding": "identity",
                              "X-Accel-Buffering": "no"})
    r.raise_for_status()
    # ensure transparent decompression doesn’t break sizes
    r.raw.decode_content = False
    return r


# def _head_content_length(url: str) -> int | None:
#     """Return Content-Length if available and parseable."""
#     try:
#         r = requests.head(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT,
#                           headers={"Accept-Encoding": "identity"})
#         r.raise_for_status()
#         cl = r.headers.get("Content-Length")
#         if cl is None:
#             return None
#         return int(cl)
#     except Exception:
#         return None


def _add_url_as_tar_member2(tar: tarfile.TarFile, url: str, arcname: str):
    """
    Add a URL to a tar stream.

    Uses true streaming (tar reads directly from the HTTP response) only when
    we have a reliable Content-Length.

    IMPORTANT:
    - For .zip, ALWAYS spool to disk to guarantee an exact size,
      which prevents truncated/corrupt tarballs when upstream connections hiccup.
    """
    url = _sanitize_local_resource_url(url)

    # Prefer GET's Content-Length (what we actually stream), not HEAD's
    # This avoids HEAD/GET mismatches through proxies/CDNs
    r = _stream_get(url)
    try:
        cl = r.headers.get("Content-Length")
        size = None
        if cl is not None:
            try:
                size = int(cl)
            except ValueError:
                size = None

        if size is not None:
            # True streaming: tar reads exactly `size` bytes from r.raw
            ti = tarfile.TarInfo(name=arcname)
            ti.size = size
            tar.addfile(ti, fileobj=r.raw)
            return
    except Exception as e:
        raise e
    finally:
        r.close()

    # Fallback: spool to disk to learn size (safe for chunked/unknown length).
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


# def _add_url_as_tar_member(tar: tarfile.TarFile, url: str, arcname: str):
#     """
#     Add a URL to a tar stream.

#     Uses Content-Length for true streaming when possible;
#     otherwise spools to disk for that member.
#     """
#     url = _sanitize_local_resource_url(url)

#     size = _head_content_length(url)
#     if size is not None:
#         # True streaming: tar reads directly from response.raw
#         r = _stream_get(url)
#         try:
#             ti = tarfile.TarInfo(name=arcname)
#             ti.size = size
#             tar.addfile(ti, fileobj=r.raw)
#         finally:
#             r.close()
#         return

#     # Fallback: spool to disk to learn size
#     with tempfile.NamedTemporaryFile(mode="w+b", delete=True) as tmp:
#         r = _stream_get(url)
#         try:
#             for chunk in r.iter_content(chunk_size=1024 * 1024):
#                 if chunk:
#                     tmp.write(chunk)
#         finally:
#             r.close()
#         tmp.flush()
#         tmp.seek(0, os.SEEK_END)
#         size = tmp.tell()
#         tmp.seek(0)

#         ti = tarfile.TarInfo(name=arcname)
#         ti.size = size
#         tar.addfile(ti, fileobj=tmp)


def _add_url_as_tar_member(tar: tarfile.TarFile, url: str, arcname: str):
    """
    Add a URL to a tar stream.

    - Prefer GET's Content-Length (not HEAD) when streaming.
    - For large files, spool to disk and verify byte count to avoid truncated tars.
    """

    url = _sanitize_local_resource_url(url)

    # First GET to see what we *actually* get back
    r = _stream_get(url)
    try:
        cl = r.headers.get("Content-Length")
        size = int(cl) if cl and cl.isdigit() else None

        # If size is known and "small enough", stream directly into the tar
        if size is not None and size < LARGE_MEMBER_BYTES: #diff from above is checking size < large bytes
            # true streaming
            ti = tarfile.TarInfo(name=arcname)
            ti.size = size
            tar.addfile(ti, fileobj=r.raw)
            return
    except Exception as e:
        raise e
    finally:
        r.close()

    # Otherwise spool to disk (and verify/optionally retry)
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        with tempfile.NamedTemporaryFile(mode="w+b", delete=True) as tmp:
            r = _stream_get(url)
            expected = None
            try:
                cl = r.headers.get("Content-Length")
                expected = int(cl) if cl and cl.isdigit() else None

                total = 0
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    tmp.write(chunk)
                    total += len(chunk)
            except Exception as e:
                last_err = e
                continue
            finally:
                r.close()

            tmp.flush()
            if expected is not None and total != expected:
                last_err = IOError(
                    f"Short read for {url}: expected {expected} bytes, got {total}"
                )
                continue  # retry

            tmp.seek(0)
            ti = tarfile.TarInfo(name=arcname)
            ti.size = total
            tar.addfile(ti, fileobj=tmp)
            return

    # If we exhausted retries, raise so it shows clearly in logs
    raise last_err or IOError(f"Failed to download {url}")


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


def _shapefile_part_filenames(shp_filename: str) -> list[str]:
    """
    Given 'foo.shp', return ['foo.shp', 'foo.dbf', ..., 'foo.shp.xml'].
    (Note: .shp.xml is optional and has a double extension.)
    """
    base = os.path.splitext(os.path.basename(shp_filename))[0]
    parts = [f"{base}{ext}" for ext in SHAPEFILE_PART_EXTS]
    parts.append(f"{base}.shp.xml")
    return parts


def _resource_shapefile_items(pkg_resources: list[dict], shp_filename: str) -> list[tuple[str, str]]:
    """
    For resource-backed shapefiles: return [(url, arcname), ...] for parts that exist in the package.
    Uses the expected filename list, then looks up matching resources by filename.
    """
    # Build filename -> url lookup from package resources
    by_filename: dict[str, str] = {}
    for r in pkg_resources:
        fname = (_filename_from_resource(r) or "").strip()
        url = r.get("url")
        if fname and url:
            by_filename[fname.lower()] = url

    items: list[tuple[str, str]] = []
    for part in _shapefile_part_filenames(shp_filename):
        url = by_filename.get(part.lower())
        if url:
            items.append((url, part))  # arcname: keep flat, or adjust if you want directories
    # Put .shp first for nicer UX
    items.sort(key=lambda it: (0 if it[1].lower().endswith(".shp") else 1, it[1].lower()))
    return items


def _shapefile_tar_members(source_name: str, source_url: str) -> list[tuple[str, str]]:
    """
    Return [(url, arcname), ...] for all expected shapefile parts,
    placed alongside source_name’s directory path.
    """
    shp_dir = os.path.dirname(source_name)
    url_dir = source_url.rsplit("/", 1)[0]

    items: list[tuple[str, str]] = []
    for part in _shapefile_part_filenames(source_name):
        arcname = os.path.join(shp_dir, part) if shp_dir else part
        url = f"{url_dir}/{part}"
        items.append((url, arcname))
    return items


def _zip_arcname_for_folderish(source_url: str, source_name: str) -> str:
    """
    folder download zip arcname normalization helper

    If gate.url points to a .zip, ensure arcname ends in .zip even if source_name is folder-ish.
    """
    url_fname = _filename_from_url(source_url) or ""
    base = os.path.basename((source_name or "").rstrip("/")) or os.path.splitext(url_fname)[0] or "folder"
    # preserve directory structure if source_name contains subdirs
    parent = os.path.dirname(source_name.rstrip("/"))
    arc = f"{base}.zip"
    return os.path.join(parent, arc) if parent else arc


def _direct_download_url(url: str, download_name: str | None = None, mimetype: str = "application/octet-stream"):
    """get direct download response"""
    if not url:
        abort(400, "Missing url")
    try:
        data = _download_bytes(url)
    except Exception:
        log.exception("Direct download failed")
        abort(502)

    filename = download_name or _filename_from_url(url) or "download"
    return send_file(BytesIO(data), as_attachment=True, download_name=filename, mimetype=mimetype)


def _stream_tar_response(out_name: str, build_tar_fn):
    """
    build a tar stream response

    build_tar_fn(tar) should add members to tar (may raise).
    Streams back a tar as application/x-tar.
    """
    headers = {"Content-Disposition": f'attachment; filename="{out_name}"',
               "Content-Type": "application/x-tar",
               "X-Accel-Buffering": "no",
               "Cache-Control": "no-store",
               "Pragma": "no-cache",
               "X-Content-Type-Options": "nosniff"}

    r_fd, w_fd = os.pipe()

    def writer():
        try:
            with os.fdopen(w_fd, "wb", buffering=0) as w:
                with tarfile.open(fileobj=w, mode="w|") as tar:
                    build_tar_fn(tar)
        except BrokenPipeError:
            log.info("Client disconnected during tar stream")
        except Exception:
            log.exception("Tar stream writer failed")
        finally:
            try:
                os.close(w_fd)
            except Exception:
                pass

    threading.Thread(target=writer, daemon=True).start()

    def generate():
        first = True
        with os.fdopen(r_fd, "rb") as r:
            while True:
                #chunk = r.read(1024 * 1024)
                chunk = r.read(16 * 1024 if first else 1024 * 1024)  # 16KB then 1MB
                first = False
                if not chunk:
                    break
                yield chunk

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-tar",
        headers=headers,
        direct_passthrough=True,
    )


def _add_manifest_first(tar: tarfile.TarFile, resource_id: str):
    payload = json.dumps({
        "resource_id": resource_id,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": "Tar streaming started successfully."
    }, indent=2).encode("utf-8")

    #pad manifest to 256 kb so browsers visibly start download immediately
    min_bytes = 256 * 1024
    if len(payload) < min_bytes:
        payload += b"\n" + b" " * (min_bytes - len(payload) - 1)

    ti = tarfile.TarInfo(name="MANIFEST.json")
    ti.size = len(payload)
    tar.addfile(ti, BytesIO(payload))

    # Force tar to write the record right now
    try:
        tar.fileobj.flush()
    except Exception:
        pass


# @bp.route("/bundle-tar/<resource_id>")
def bundle_tar_prev(resource_id):
    """Return a .tar bundle = data file + metadata yaml if present."""
    ctx = {"for_view": True}
    try:
        data_res = toolkit.get_action("resource_show")(ctx, {"id": resource_id})
    except toolkit.ObjectNotFound:
        abort(404)

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

    meta_res_url = meta_res.get("url") if meta_res else None
    if not name.lower().endswith(".shp") and not meta_res_url:
        # Direct-download fallback for non-shp single files w/out metadata
        filename = _filename_from_resource(data_res) or "download"
        return _direct_download_url(data_res.get("url"), download_name=filename)

    # Decide what data files to include
    if name.lower().endswith(".shp"):
        data_items = _resource_shapefile_items(resources, name)
    else:
        data_items = [(data_res.get("url"),
                       _filename_from_resource(data_res) or "file")] #prev: or name

    out_name = f"{os.path.basename(name.replace('.', '_'))}_with_metadata.tar"

    def build(tar):
        # add metadata if it exists
        if meta_res_url:
            _add_url_as_tar_member(
                tar, meta_res_url,
                _filename_from_resource(meta_res) or f"{name}.yml")
        for url, arcname in [(u, a) for (u, a) in data_items if u]:
            # data_items = [(data_res.get("url"),
            #               _filename_from_resource(data_res) or name)]

            if not url:
                continue
            _add_url_as_tar_member(tar, url, arcname)

    return _stream_tar_response(out_name, build)

@bp.route("/bundle-tar/<resource_id>")
def bundle_tar(resource_id):
    """Return a .tar bundle = data file + metadata yaml if present (manifest-first)."""

    def build(tar):
        #  First bytes immediately
        _add_manifest_first(tar, resource_id)

        ctx = {"for_view": True}
        try:
            data_res = toolkit.get_action("resource_show")(ctx, {"id": resource_id})
        except toolkit.ObjectNotFound:
            abort(404)

        pkg = toolkit.get_action("package_show")({}, {"id": data_res["package_id"]})
        resources = pkg.get("resources", []) or []

        name = data_res.get("name") or os.path.basename(data_res.get("url", "")) or "resource"

        # Find attached YAML matching filename
        candidates = [f"{name}.yml", f"{name}.yaml"]
        meta_res = None
        for r in resources:
            rname = _filename_from_resource(r)
            if rname in candidates:
                meta_res = r
                break

        meta_res_url = meta_res.get("url") if meta_res else None

        # Decide what data files to include
        if name.lower().endswith(".shp"):
            data_items = _resource_shapefile_items(resources, name)
        else:
            data_items = [(data_res.get("url"),
                           _filename_from_resource(data_res) or "file")]

        # Add metadata first if it exists
        if meta_res_url:
            _add_url_as_tar_member(
                tar, meta_res_url,
                _filename_from_resource(meta_res) or f"{name}.yml")

        # Add data members
        for url, arcname in [(u, a) for (u, a) in data_items if u]:
            _add_url_as_tar_member(tar, url, arcname)

    # out_name = f"{resource_id}_with_metadata.tar"
    data_res = toolkit.get_action("resource_show")({"for_view": True},
                                                   {"id": resource_id})
    name = data_res.get("name") or os.path.basename(data_res.get("url", "")) or "resource"
    out_name = f"{os.path.basename(name.replace('.', '_'))}_with_metadata.tar"
    return _stream_tar_response(out_name, build)


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

    # Direct download (no tar) if no metadata and not a shapefile
    if not meta_url and not source_name.lower().endswith(".shp"):
        url_fname = _filename_from_url(source_url)
        src_base = os.path.basename((source_name or "").rstrip("/")) or "download"

        # If the URL is a zip (folder download), ensure the downloaded filename ends with .zip
        if url_fname.lower().endswith(".zip") and not src_base.lower().endswith(".zip"):
            filename = f"{src_base}.zip"
            mimetype = "application/zip"
        else:
            # Prefer the URL filename if source_name is extensionless
            filename = src_base
            if "." not in src_base and url_fname:
                filename = url_fname
            mimetype = "application/octet-stream"

        return _direct_download_url(source_url, download_name=filename,
                                    mimetype=mimetype)

    out_name = f"{os.path.basename(source_name.replace('.', '_'))}_with_metadata.tar"

    def build(tar):
        # data members
        if source_name.lower().endswith(".shp"):
            # best effort to add shapefile sidecar files
            for url, arcname in _shapefile_tar_members(source_name,
                                                       source_url):
                try:
                    _add_url_as_tar_member(tar, url, arcname)
                except requests.HTTPError as e:
                    code = getattr(e.response, "status_code", None)
                    if code in (403, 404):
                        continue
                    raise
        else:
            url_fname = _filename_from_url(source_url) or ""
            if url_fname.lower().endswith(".zip"):
                arcname = _zip_arcname_for_folderish(source_url, source_name)
                _add_url_as_tar_member(tar, source_url, arcname)
            else:
                _add_url_as_tar_member(tar, source_url, source_name)

        # metadata member
        if meta_url:
            try:
                _add_url_as_tar_member(tar, meta_url, meta_name)
            except requests.HTTPError as e:
                code = getattr(e.response, "status_code", None)
                if code not in (403, 404):
                    raise

    return _stream_tar_response(out_name, build)