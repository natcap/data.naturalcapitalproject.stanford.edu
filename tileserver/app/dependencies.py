"""dependencies.

app/dependencies.py

"""

import json
from typing import Dict
from typing import Literal
from typing import Optional
from typing import Sequence

import matplotlib
import numpy
from fastapi import HTTPException
from fastapi import Query
from rio_tiler.colormap import cmap as default_cmap
from rio_tiler.colormap import parse_color
from rio_tiler.models import ImageData
from rio_tiler.types import ColorMapType
from starlette.responses import Response
from titiler.core.factory import ColorMapFactory
from titiler.core.resources.enums import ImageType
from typing_extensions import Annotated


class NatCapColorMapFactory(ColorMapFactory):
    def register_routes(self):
        """Register colormap routes, derived from ColorMapFactory."""
        # inherit all of the normal routes.
        ColorMapFactory.register_routes(self)

        # Add our own route.
        @self.router.get(
            "/colorMapCustom",
            response_model=ColorMapType,
            summary="Retrieve a custom colorMap metadata or image.",
            operation_id="getCustomColorMap",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                        "image/png": {},
                        "image/jpeg": {},
                        "image/jpg": {},
                        "image/webp": {},
                        "image/jp2": {},
                        "image/tiff; application=geotiff": {},
                        "application/x-binary": {},
                    },
                },
            },
        )
        def colormap_custom(
            colormap: Annotated[
                str,
                Query(description="JSON encoded custom Colormap"),
            ],
            colormap_type: Annotated[
                Literal["explicit", "linear"],
                Query(description="User input colormap type."),
            ] = "explicit",
            # image output options
            format: Annotated[
                Optional[ImageType],
                Query(
                    description="Return colorMap as Image.",
                ),
            ] = None,
            orientation: Annotated[
                Optional[Literal["vertical", "horizontal"]],
                Query(
                    description="Image Orientation.",
                ),
            ] = None,
            height: Annotated[
                Optional[int],
                Query(
                    description="Image Height (default to 20px for horizontal or 256px for vertical).",
                ),
            ] = None,
            width: Annotated[
                Optional[int],
                Query(
                    description="Image Width (default to 256px for horizontal or 20px for vertical).",
                ),
            ] = None,
            ) -> Optional[Dict]:
            """Custom user-defined colormap endpoint."""
            cmap = ColorMapParams(colormap=colormap, colormap_type=colormap_type)
            ###############################################################
            # SEQUENCE CMAP
            if isinstance(cmap, Sequence):
                values = [minv for ((minv, _), _) in cmap]
                arr = numpy.array([values] * 20)

                if orientation == "vertical":
                    height = height or 256 if len(values) < 256 else len(values)
                else:
                    width = width or 256 if len(values) < 256 else len(values)

            ###############################################################
            # DISCRETE CMAP
            elif len(cmap) != 256 or max(cmap) >= 256 or min(cmap) < 0:
                values = list(cmap)
                arr = numpy.array([values] * 20)

                if orientation == "vertical":
                    height = height or 256 if len(values) < 256 else len(values)
                else:
                    width = width or 256 if len(values) < 256 else len(values)

            ###############################################################
            # LINEAR CMAP
            else:
                cmin, cmax = min(cmap), max(cmap)
                arr = numpy.array(
                    [
                        numpy.round(numpy.linspace(cmin, cmax, num=256)).astype(
                            numpy.uint8
                        )
                    ]
                    * 20
                )

            if orientation == "vertical":
                arr = arr.transpose([1, 0])

            img = ImageData(arr)

            width = width or img.width
            height = height or img.height
            if width != img.width or height != img.height:
                img = img.resize(height, width)

            return Response(
                img.render(img_format=format.driver, colormap=cmap),
                media_type=format.mediatype,
            )

            if isinstance(cmap, Sequence):
                return [(k, numpy.array(v).tolist()) for (k, v) in cmap]
            else:
                return {k: numpy.array(v).tolist() for k, v in cmap.items()}


def ColorMapParams(
            colormap_name: Annotated[  # type: ignore
                Literal[tuple(default_cmap.list())],
                Query(description="Colormap name"),
            ] = None,
            colormap: Annotated[
                str,
                Query(description="JSON encoded custom Colormap"),
            ] = None,
            colormap_type: Annotated[
                Literal["explicit", "linear"],
                Query(description="User input colormap type."),
            ] = "explicit",
        ) -> Optional[Dict]:
    """Colormap Dependency."""
    if colormap_name:
        return default_cmap.get(colormap_name)

    if colormap:
        try:
            cm = json.loads(
                colormap,
                object_hook=lambda x: {int(k): parse_color(v) for k, v in x.items()},
            )
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400, detail="Could not parse the colormap value."
            )

        if colormap_type == "linear":
            # input colormap has to start from 0 to 255 ?
            cm = matplotlib.colors.LinearSegmentedColormap.from_list(
                'custom',
                [
                    (k / 255, matplotlib.colors.to_hex([v / 255 for v in rgba], keep_alpha=True))
                    for (k, rgba) in cm.items()
                ],
                256,
            )
            x = numpy.linspace(0, 1, 256)
            cmap_vals = cm(x)[:, :]
            cmap_uint8 = (cmap_vals * 255).astype('uint8')
            cm = {idx: value.tolist() for idx, value in enumerate(cmap_uint8)}

        return cm

    return None


ALLOWED_PREFIXES = (
    'https://storage.googleapis.com/natcap-data-cache',
    'https://data.naturalcapitalalliance.stanford.edu',
    'http://data.naturalcapitalalliance.stanford.edu',
    'https://data.naturalcapitalproject.stanford.edu',
    'http://data.naturalcapitalproject.stanford.edu'
)

def DatasetPathParams(url: Annotated[str, Query(description="Dataset URL")]) -> str:
    """Create dataset path from args"""
    if not url.startswith(ALLOWED_PREFIXES):
        raise HTTPException(
            status_code=401,
            detail="Access denied; please use an allowed dataset URL."
        )
    return url
