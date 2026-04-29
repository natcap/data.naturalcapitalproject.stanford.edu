"""titiler app.

This was downloaded from https://github.com/developmentseed/titiler/blob/main/src/titiler/application/titiler/application/main.py
at commit 575cb7259431bb489a3ebb2f233a866c7fc5146b in order to provide the same basic functionality as titiler, but with a custom colormap.

This file (minus modifications) was originally licensed under the MIT license.
"""

import logging
import os
import re
import sys

import dependencies
import google.cloud.logging
import jinja2
from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Security
from fastapi.security.api_key import APIKeyQuery
from rio_tiler.io import Reader
from rio_tiler.io import STACReader
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.templating import Jinja2Templates
from starlette_cramjam.middleware import CompressionMiddleware
from titiler.application import __version__ as titiler_version
from titiler.application.settings import ApiSettings
from titiler.core.errors import add_exception_handlers
from titiler.core.errors import DEFAULT_STATUS_CODES
from titiler.core.factory import AlgorithmFactory
from titiler.core.factory import MultiBaseTilerFactory
from titiler.core.factory import TMSFactory
from titiler.core.middleware import CacheControlMiddleware
from titiler.core.middleware import LoggerMiddleware
from titiler.core.middleware import LowerCaseQueryStringMiddleware
from titiler.core.middleware import TotalTimeMiddleware
from titiler.extensions import cogValidateExtension
from titiler.extensions import cogViewerExtension
from titiler.extensions import stacExtension
from titiler.extensions import stacViewerExtension
from titiler.mosaic.errors import MOSAIC_STATUS_CODES
from titiler.mosaic.factory import MosaicTilerFactory

#from cache import setup_cache

#logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

try:
    # Instantiates a client
    client = google.cloud.logging.Client()

    # Retrieves a Cloud Logging handler based on the environment
    # you're running in and integrates the handler with the
    # Python logging module. By default this captures all logs
    # at INFO level and higher
    client.setup_logging()
except Exception as e:
    LOGGER.info(f"Skipping GCP cloud logging setup; no credentials found. {str(e)}")

logging.getLogger("botocore.credentials").disabled = True
logging.getLogger("botocore.utils").disabled = True
logging.getLogger("rio-tiler").setLevel(logging.ERROR)
logging.getLogger("rasterio").setLevel(logging.ERROR)

jinja2_env = jinja2.Environment(
    loader=jinja2.ChoiceLoader([jinja2.PackageLoader('titiler.application', "templates")])
)
templates = Jinja2Templates(env=jinja2_env)


api_settings = ApiSettings()

###############################################################################
# Setup a global API access key, if configured
api_key_query = APIKeyQuery(name="access_token", auto_error=False)


def validate_access_token(access_token: str = Security(api_key_query)):
    """Validates API key access token, set as the `api_settings.global_access_token` value.
    Returns True if no access token is required, or if the access token is valid.
    Raises an HTTPException (401) if the access token is required but invalid/missing.
    """
    if api_settings.global_access_token is None:
        return True

    if not access_token:
        raise HTTPException(status_code=401, detail="Missing `access_token`")

    # if access_token == `token` then OK
    if access_token != api_settings.global_access_token:
        raise HTTPException(status_code=401, detail="Invalid `access_token`")

    return True


###############################################################################

app = FastAPI(
    title=api_settings.name,
    openapi_url="/api",
    docs_url="/api.html",
    description="""A modern dynamic tile server built on top of FastAPI and Rasterio/GDAL.

---

**Documentation**: <a href="https://developmentseed.org/titiler/" target="_blank">https://developmentseed.org/titiler/</a>

**Source Code**: <a href="https://github.com/developmentseed/titiler" target="_blank">https://github.com/developmentseed/titiler</a>

---
    """,
    version=titiler_version,
    root_path=api_settings.root_path,
    dependencies=[Depends(validate_access_token)],
)


###############################################################################
# Simple Dataset endpoints (e.g Cloud Optimized GeoTIFF)
if not api_settings.disable_cog:
    if os.path.exists('/app'):
        sys.path.append('/app')

    ## Setup Cache
    #app.add_event_handler('startup', setup_cache)

    from dependencies import ColorMapParams
    from dependencies import DatasetPathParams
    from routes import TilerFactory

    cog = TilerFactory(
        reader=Reader,
        router_prefix="/cog",
        colormap_dependency=ColorMapParams,
        path_dependency=DatasetPathParams,
        extensions=[
            cogValidateExtension(),
            cogViewerExtension(),
            stacExtension(),
        ],
    )

    app.include_router(
        cog.router,
        prefix="/cog",
        tags=["Cloud Optimized GeoTIFF"],
    )


###############################################################################
# STAC endpoints
if not api_settings.disable_stac:
    stac = MultiBaseTilerFactory(
        reader=STACReader,
        router_prefix="/stac",
        extensions=[
            stacViewerExtension(),
        ],
    )

    app.include_router(
        stac.router,
        prefix="/stac",
        tags=["SpatioTemporal Asset Catalog"],
    )

###############################################################################
# Mosaic endpoints
if not api_settings.disable_mosaic:
    mosaic = MosaicTilerFactory(router_prefix="/mosaicjson")
    app.include_router(
        mosaic.router,
        prefix="/mosaicjson",
        tags=["MosaicJSON"],
    )

###############################################################################
# TileMatrixSets endpoints
tms = TMSFactory()
app.include_router(
    tms.router,
    tags=["Tiling Schemes"],
)

###############################################################################
# Algorithms endpoints
algorithms = AlgorithmFactory()
app.include_router(
    algorithms.router,
    tags=["Algorithms"],
)

###############################################################################
# Colormaps endpoints
cmaps = dependencies.NatCapColorMapFactory()
app.include_router(
    cmaps.router,
    tags=["ColorMaps"],
)


add_exception_handlers(app, DEFAULT_STATUS_CODES)
add_exception_handlers(app, MOSAIC_STATUS_CODES)

# Set all CORS enabled origins
if api_settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=api_settings.cors_origins,
        allow_credentials=True,
        allow_methods=api_settings.cors_allow_methods,
        allow_headers=["*"],
    )

app.add_middleware(
    CompressionMiddleware,
    minimum_size=0,
    exclude_mediatype={
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/jp2",
        "image/webp",
    },
    compression_level=6,
)

app.add_middleware(
    CacheControlMiddleware,
    cachecontrol=api_settings.cachecontrol,
    exclude_path={r"/healthz"},
)

if api_settings.debug:
    app.add_middleware(LoggerMiddleware, headers=True, querystrings=True)
    app.add_middleware(TotalTimeMiddleware)

if api_settings.lower_case_query_parameters:
    app.add_middleware(LowerCaseQueryStringMiddleware)


@app.get(
    "/healthz",
    description="Health Check.",
    summary="Health Check.",
    operation_id="healthCheck",
    tags=["Health Check"],
)
def ping():
    """Health check."""
    return {"ping": "pong!"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing(request: Request):
    """TiTiler landing page."""
    data = {
        "title": "titiler",
        "links": [
            {
                "title": "Landing page",
                "href": str(request.url_for("landing")),
                "type": "text/html",
                "rel": "self",
            },
            {
                "title": "the API definition (JSON)",
                "href": str(request.url_for("openapi")),
                "type": "application/vnd.oai.openapi+json;version=3.0",
                "rel": "service-desc",
            },
            {
                "title": "the API documentation",
                "href": str(request.url_for("swagger_ui_html")),
                "type": "text/html",
                "rel": "service-doc",
            },
            {
                "title": "TiTiler Documentation (external link)",
                "href": "https://developmentseed.org/titiler/",
                "type": "text/html",
                "rel": "doc",
            },
            {
                "title": "TiTiler source code (external link)",
                "href": "https://github.com/developmentseed/titiler",
                "type": "text/html",
                "rel": "doc",
            },
        ],
    }

    urlpath = request.url.path
    if root_path := request.app.root_path:
        urlpath = re.sub(r"^" + root_path, "", urlpath)
    crumbs = []
    baseurl = str(request.base_url).rstrip("/")

    crumbpath = str(baseurl)
    for crumb in urlpath.split("/"):
        crumbpath = crumbpath.rstrip("/")
        part = crumb
        if part is None or part == "":
            part = "Home"
        crumbpath += f"/{crumb}"
        crumbs.append({"url": crumbpath.rstrip("/"), "part": part.capitalize()})

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "response": data,
            "template": {
                "api_root": baseurl,
                "params": request.query_params,
                "title": "TiTiler",
            },
            "crumbs": crumbs,
            "url": str(request.url),
            "baseurl": baseurl,
            "urlpath": str(request.url.path),
            "urlparams": str(request.url.query),
        },
    )

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        'main:app',
        host=os.environ.get('HOST', '127.0.0.1'),
        port=int(os.environ.get('PORT', 8000)),
        log_level='info',
    )
