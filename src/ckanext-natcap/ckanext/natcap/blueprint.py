from flask import Blueprint, Response, abort, request, \
    stream_with_context, redirect
from ckan.plugins import toolkit
import requests, os
from io import BytesIO
import ipaddress
import json
import logging
import re
import socket
import tarfile
import tempfile
import threading
import time
from urllib.parse import urlparse

log = logging.getLogger(__name__)
bp = Blueprint("natcap", __name__)


SHAPEFILE_PART_EXTS = [
    ".shp", ".dbf", ".shx", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".shp.xml"
]

DEFAULT_TIMEOUT = (60, 7200)  # (connect, read) seconds

ALLOWED_URL_HOSTS = {
    "localhost",
    "ckan",
    "data.naturalcapitalalliance.stanford.edu",
    "storage.googleapis.com",
}
ALLOWED_GCS_BUCKET = "natcap-data-cache"
INTERNAL_CKAN_PORT = int(os.environ.get("CKAN_PORT", "5000"))
ALLOWED_LOCAL_PORTS = {INTERNAL_CKAN_PORT, 8443, 80, 443}


def _host_allowed(host: str) -> bool:
    if not host:
        return False
    host = host.lower().strip(".")
    return host in ALLOWED_URL_HOSTS


def _host_resolves_to_public_ips(host: str) -> bool:
    """Resolve host and ensure *all* A/AAAA records are public-ish."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False

    addrs = {info[4][0] for info in infos}  # unique IP strings
    for ip_str in addrs:
        ip = ipaddress.ip_address(ip_str)
        # reject multicast/unspecified/reserved/private/linklocal/etc
        if any((ip.is_private, ip.is_loopback, ip.is_link_local,
                ip.is_multicast, ip.is_unspecified, ip.is_reserved)):
            return False
    return True


def _validate_outbound_url(url: str) -> str:
    """Return the URL if allowed; otherwise abort."""
    if not url:
        abort(400, "Missing URL")

    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        abort(400, "Invalid URL scheme")
    if not u.hostname:
        abort(400, "Invalid URL host")

    host = u.hostname.lower().strip(".")
    if not _host_allowed(host):
        abort(403, "Host not allowed")
    if host == "storage.googleapis.com":
        parts = (u.path or "").lstrip("/").split("/", 1)
        # assumes URLS are /<bucket>/<object>, not JSON API form (/storage/v1/..)
        bucket_name = parts[0] if parts else ""
        if bucket_name != ALLOWED_GCS_BUCKET:
            abort(403, "GCS bucket not allowed")

    # allow internal hosts without public ip check
    if host not in ("localhost", "ckan"):
        if not _host_resolves_to_public_ips(host):
            abort(403, "Host resolves to a disallowed IP range")
        if u.port and u.port not in (80, 443):
            abort(403, "Port not allowed")
    else:
        if u.port and u.port not in ALLOWED_LOCAL_PORTS:
            abort(403, "Port not allowed")

    return url


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    return os.path.basename(path) or "file"


def _stream_get(url: str) -> requests.Response:
    """GET streaming response (caller must close)."""
    url = _validate_outbound_url(url)
    r = requests.get(url, stream=True, timeout=DEFAULT_TIMEOUT,
                     headers={"Accept-Encoding": "identity",
                              "X-Accel-Buffering": "no"})
    r.raise_for_status()
    # ensure transparent decompression doesn’t break sizes
    r.raw.decode_content = False
    return r


def _add_url_as_tar_member(tar: tarfile.TarFile, url: str, arcname: str):
    """
    Add a URL to a tar stream.

    - If Content-Length is present, try streaming.
    - Otherwise, spool to a temp file.
    """

    url = _sanitize_local_resource_url(url)

    # First GET to see what we *actually* get back
    r = _stream_get(url)
    try:
        cl = r.headers.get("Content-Length")
        size = int(cl) if cl and cl.isdigit() else None

        # If size is known, stream directly into the tar
        if size is not None:
            ti = tarfile.TarInfo(name=arcname)
            ti.size = size
            try:
                tar.addfile(ti, fileobj=r.raw)
                return
            except Exception as e:
                log.warning("Streaming %s failed, fall back to spooling: %s",
                            url, e)

        else:
            log.warning("No Content-Length for %s; will spool", url)
    finally:
        r.close()

    # Need to re-get bc if streaming failed mid-transfer, response is
    # partially consumed/broken.
    r = _stream_get(url)
    try:
        with tempfile.NamedTemporaryFile(mode="w+b", delete=True) as tmp:
            total = 0
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    tmp.write(chunk)
                    total += len(chunk)
            tmp.flush()
            tmp.seek(0)
            ti = tarfile.TarInfo(name=arcname)
            ti.size = total
            tar.addfile(ti, fileobj=tmp)
            return
    finally:
        r.close()


def _sanitize_local_resource_url(resource_url: str) -> str:
    """
    Normalize resource URLs so they're reachable from inside CKAN container.

    - If URL is like https://localhost:8443/... (dev via nginx),
      rewrite to http://localhost:{INTERNAL_CKAN_PORT}/...
    """
    if not resource_url:
        return resource_url

    # Match http(s)://localhost[:port]/path...
    m = re.match(r'^https?://localhost(?::\d+)?/(.*)$', resource_url)
    if m:
        path_only = m.group(1)  # e.g. 'dataset/...'
        # IMPORTANT: use the internal CKAN address
        internal_base = f'http://localhost:{INTERNAL_CKAN_PORT}'
        return f"{internal_base.rstrip('/')}/{path_only.lstrip('/')}"

    # All other URLs (e.g. GCS, s3, real domains) pass through unchanged
    return resource_url


def _filename_from_resource(res: dict) -> str:
    # Prefer CKAN resource.name, fallback to URL basename
    n = res.get("name", "").strip()
    if n:
        return n
    url = res.get("url", "")
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
    return parts


def _resource_shapefile_items(pkg_resources: list[dict],
                              shp_filename: str) -> list[tuple[str, str]]:
    """
    For resource-backed shapefiles: return [(url, arcname), ...] for parts
    that exist in the package. Uses the expected filename list, then looks
    up matching resources by filename.
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
            items.append((url, part))
    # Put .shp first for nicer UX
    items.sort(key=lambda it: (
        0 if it[1].lower().endswith(".shp") else 1, it[1].lower()))
    return items


def _shapefile_tar_members(source_name: str,
                           source_url: str) -> list[tuple[str, str]]:
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

    If gate.url points to a .zip, ensure arcname ends in .zip even
    if source_name is folder-ish.
    """
    url_fname = _filename_from_url(source_url) or ""
    base = os.path.basename(
        (source_name or "").rstrip("/")) or os.path.splitext(url_fname)[0] or "folder"
    # preserve directory structure if source_name contains subdirs
    parent = os.path.dirname(source_name.rstrip("/"))
    arc = f"{base}.zip"
    return os.path.join(parent, arc) if parent else arc


def _proxy_stream(url: str, download_name: str, mimetype: str):
    # Accept relative /dataset/... by turning it into internal CKAN URL
    if url.startswith("/") and not url.startswith("//"):
        safe_url = f"http://localhost:{INTERNAL_CKAN_PORT}{url}"
    else:
        # Rewrite localhost dev URLs to internal CKAN port
        safe_url = _sanitize_local_resource_url(url)

    r = _stream_get(safe_url)

    def gen():
        try:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            r.close()

    clean_download_name = download_name.replace(
        '"', '_').replace("\n", "").replace("\r", "")
    headers = {
        "Content-Disposition": f'attachment; filename="{clean_download_name}"',
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-store",
    }
    return Response(stream_with_context(gen()), headers=headers,
                    mimetype=mimetype)


def _direct_download_url(
        url: str, download_name: str | None = None,
        mimetype: str = "application/octet-stream") -> Response:
    """
    Redirect to the given URL for direct download (preferred), or proxy stream.

    Return a redirect to the underlying file unless the browser will try to
    directly display (and not download) it (based on  filename extension, not
    response header), in which case, proxy bytes through CKAN.
    """
    if not url:
        abort(400, "Missing url")

    filename = download_name or _filename_from_url(url) or "download"
    log.info("Direct download requested url=%s download_name=%s",
             url, filename)

    # Extensions that browsers typically try to display inline
    INLINE_EXTENSIONS = ('.txt', '.log', '.csv', '.pdf', '.json',
                         '.xml', '.html')

    # Determine if we should proxy or redirect
    is_browser_viewable = filename.lower().endswith(INLINE_EXTENSIONS)
    if is_browser_viewable:
        log.info("Forcing download via proxy for viewable file: %s", filename)
        return _proxy_stream(url, filename, mimetype)

    if url.startswith("/") and not url.startswith("//"):
        resp = redirect(url, code=302)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # Keep the SSRF checks (host allowlist, ports, etc.)
    safe = _validate_outbound_url(url)
    u = urlparse(safe)
    host = (u.hostname or "").lower().strip(".")
    log.info("Direct download host: %s", host)
    log.info("Direct download port: %s", u.port)

    # Localhost is only client-reachable in dev (e.g. https://localhost:8443)
    if host == "localhost" and u.port in ALLOWED_LOCAL_PORTS:
        log.info("Host is local and port is 5000 or 8443")
        target = u.path or "/"
        if u.query:
            target += "?" + u.query
        resp = redirect(target, code=302)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    resp = redirect(safe, code=302)
    resp.headers["Cache-Control"] = "no-store"
    return resp


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

    threading.Thread(target=writer, daemon=True).start()

    def generate():
        with os.fdopen(r_fd, "rb") as r:
            yield r.read(16 * 1024)  # 16KB
            while True:
                chunk = r.read(1024 * 1024)  # 1MB
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
    # Stream a POSIX-compatible tar archive over HTTP using standard
    # sequential tar semantics and chunked HTTP responses
    payload = json.dumps({
        "resource_id": resource_id,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": "Tar streaming started successfully."
    }, indent=2).encode("utf-8")
    # ^ json.dumps returns text; tarfile requires bytes

    # Pad manifest to 256 kb so browsers visibly start download immediately
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


@bp.route("/bundle-tar/<resource_id>")
def bundle_tar(resource_id):
    """
    Download a CKAN top-level resource (has a resource id) as either:
      1) a streamed .tar containing the data file(s) + attached YAML metadata, or
      2) a direct file download (no tar) if no metadata and not a shapefile.

    Using a CKAN resource id:
    - If attached metadata exists in the same package with filename
      '<resource_filename>.yml' or '<resource_filename>.yaml', it is included.
    - If resource is a shapefile (.shp), all sibling shapefile parts from the
      same package are included (e.g., .shp, .dbf, .shx, .prj, .cpg, etc.) too

    If the resource is not a shapefile and has no attached YAML metadata, this
    endpoint returns the raw file directly (via redirect when possible,
    otherwise proxied with attachment headers) instead of wrapping it in
    a .tar.

    Streaming avoids loading large files into memory and supports
    large multi-GB downloads.
    """
    ctx = {"for_view": True}
    try:
        data_res = toolkit.get_action("resource_show")(ctx, {"id": resource_id})
    except toolkit.ObjectNotFound:
        abort(404)

    pkg = toolkit.get_action("package_show")({}, {"id": data_res["package_id"]})
    resources = pkg.get("resources", []) or []

    name = data_res.get("name") or os.path.basename(data_res.get("url", "")) or "resource"
    out_name = f"{os.path.basename(name.replace('.', '_'))}_with_metadata.tar"

    candidates = [f"{name}.yml", f"{name}.yaml"]
    meta_res = None
    for r in resources:
        rname = _filename_from_resource(r)
        if rname in candidates:
            meta_res = r
            break

    meta_res_url = meta_res.get("url") if meta_res else None

    # If no metadata and not shapefile: redirect directly
    # print("meta_res_url", meta_res_url, name, "is name")
    if not meta_res_url and not name.lower().endswith(".shp"):
        log.info("Direct download for resource %s", name)
        download_url = data_res.get("url")
        dl_name = _filename_from_resource(data_res) or name or "download"
        return _direct_download_url(download_url, download_name=dl_name)

    def build(tar):
        # Add bytes immediately to show download started in browser
        _add_manifest_first(tar, resource_id)

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

    return _stream_tar_response(out_name, build)


@bp.route("/bundle-source-tar")
def bundle_source_tar():
    """
    Download a [zip-expanded] source (i.e., a "source" item that is not a CKAN
    resource id) as either:
      1) a streamed .tar with dataset + YAML metadata, if present, or
      2) a direct file download (no tar) when bundling is unnecessary.

    This function is similar to /bundle-tar/<resource_id> but works with
    source URLs instead of CKAN resource ids.

    Inputs (query parameters)
    Required:
      - source_url: URL to the source payload to download.
    Optional:
      - source_name: desired archive member name/path (or filename
                     when direct-downloading).
      - meta_url: URL to an associated YAML metadata file.
      - meta_name: desired YAML filename/path inside the tar (defaults
                   to '<source_name>.yml').

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
        _add_manifest_first(tar, f"source:{source_name}")

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
