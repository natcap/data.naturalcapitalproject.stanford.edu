"""dependencies.

app/dependencies.py

"""

import json
from typing import Dict
from typing import Literal
from typing import Optional

import matplotlib
import numpy
from fastapi import HTTPException
from fastapi import Query
from rio_tiler.colormap import cmap as default_cmap
from rio_tiler.colormap import parse_color
from typing_extensions import Annotated


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
    'http://data.naturalcapitalproject.stanford.edu',

)

def DatasetPathParams(url: Annotated[str, Query(description="Dataset URL")]) -> str:
    """Create dataset path from args"""
    if not url.startswith(ALLOWED_PREFIXES):
        raise HTTPException(
            status_code=401,
            detail="Access denied; please use an allowed dataset URL."
        )
    return url
