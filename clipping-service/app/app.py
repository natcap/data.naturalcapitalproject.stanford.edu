import datetime
import functools
import json
import logging
import logging.handlers
import os
import queue
import re
import subprocess
import time
import uuid

import flask
import humanize
import pygeoprocessing
import pygeoprocessing.geoprocessing
import requests
import yaml
from flask import jsonify
from flask import request
from flask_cors import CORS
from google.cloud import storage
from osgeo import gdal
from osgeo import osr

app = flask.Flask(__name__, template_folder='templates')
CORS(app, resources={
    '/*': {
        'origins': [
            # Only use localhost for local development.
            # 'http://localhost:*',
            # 'http://127.0.0.1:*',
            'https://data-staging.naturalcapitalproject.org',
            'https://data.naturalcapitalproject.stanford.edu',
        ]
    }
})
logging.basicConfig(level=logging.DEBUG)


# PLAN: write a logging handler to listen for progress messages and send those
# to the client.

# TODO: write a logging filter to filter out logging that isn't
# relevant to the current context (in case we have multiple clipping operations
# on the same process)

SOURCE_LOGGER = logging.getLogger('pygeoprocessing')
SOURCE_LOGGER.setLevel(logging.DEBUG)
GOOGLE_STORAGE_URL = 'https://storage.googleapis.com'
TRUSTED_BUCKET = f'{GOOGLE_STORAGE_URL}/natcap-data-cache'
TARGET_FILE_BUCKET = 'gs://jupyter-app-temp-storage'
TARGET_BUCKET_URL = f'{GOOGLE_STORAGE_URL}/jupyter-app-temp-storage'
WORKSPACE_DIR = os.environ.get('WORKSPACE_DIR', os.getcwd())
app.logger.info("WORKSPACE_DIR: %s", WORKSPACE_DIR)
pygeoprocessing.geoprocessing._LOGGING_PERIOD = 1.0


def _epsg_to_wkt(epsg_code):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(epsg_code))
    return srs.ExportToWkt()


@functools.lru_cache
def cached_raster_info(vsi_raster_path):
    try:
        return pygeoprocessing.get_raster_info(vsi_raster_path)
    except Exception:
        app.logger.error("Failed to read raster info for %s", vsi_raster_path)
        raise


def _align_bbox(bbox, raster_info):
    target_bbox = bbox[:]
    align_pixel_size = raster_info['pixel_size']
    align_bounding_box = raster_info['bounding_box']
    for index in [0, 1]:
        n_pixels = int(
            (target_bbox[index] - align_bounding_box[index]) /
            float(align_pixel_size[index]))
        target_bbox[index] = (
            n_pixels * align_pixel_size[index] +
            align_bounding_box[index])
    app.logger.info("Aligned bounding box from %s to %s",
                    bbox, target_bbox)
    return target_bbox


@app.route('/epsg_info', methods=['GET'])
def epsg_info():
    epsg_code = request.args.get('epsg_code')
    try:
        epsg_code = int(epsg_code)
    except ValueError:
        return jsonify({
            "status": "failure",
            "epsg_name": "",
            "srs_units": "",
        })

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg_code)
    srs_name = srs.GetName()
    if srs.IsGeographic():
        srs_units = srs.GetAngularUnitsName()
    else:
        srs_units = srs.GetLinearUnitsName()

    if srs_name in {None, 'unknown'}:
        srs_name = f"EPSG:{epsg_code} not recognized"
        srs_units = "unknown"

    return jsonify({
        "status": "success",
        "epsg_name": srs_name,
        "srs_units": srs_units,
    })


@app.route('/metadata', methods=['GET'])
def metadata():
    cog_url = request.args.get('cog_url')

    yaml_data = yaml.load(
        requests.get(f'{cog_url}.yml', stream=True).content,
        Loader=yaml.Loader)
    return yaml_data


@app.route('/info', methods=['GET'])
@functools.lru_cache
def info():
    cog_url = request.args.get("cog_url")
    result = gdal.Info(f'/vsicurl/{cog_url}', options=['-json'])

    return jsonify({
        'status': 'success',
        'info': result,
    })


@app.route('/hello')
def hello_world():
    return 'Hello, World!'


@app.route('/clip', methods=['GET'])
def clip_app():
    return flask.render_template('clip.html')


@app.route("/clip", methods=['POST'])
def clip():
    """Clip a COG to a bounding box.

    All parameters are passed as json args.

    Args:
        cog_url (str): The URL of the cog to clip.  Must be located on a
            trusted bucket.
        target_bbox (list): A list of the target bounding box, in the form
            minx, miny, maxx, maxy.
        target_epsg (str, int): The EPSG code of the clipped raster's target
            projection.
        target_cellsize (list): A 2-tuple of the target cell size of the
            clipped/reprojected COG, in the form (x size, y size).  If ``y
            size`` is not negative, it will be converted to a negative value.

    Returns:
        A JSON response body with the attributes:

            * ``url``: The public URL of the clipped raster
            * ``size``: A human-readable string representing the filesize of
                the clipped raster.
    """
    parameters = request.get_json()
    app.logger.info(parameters)

    if not parameters['cog_url'].startswith(TRUSTED_BUCKET):
        app.logger.error("Invalid source raster, not in the known bucket: %s",
                         parameters['cog_url'])
        raise ValueError("Invalid COG provided.")

    target_bbox = parameters["target_bbox"]

    # align the bounding box to a raster grid
    source_raster_path = f'/vsicurl/{parameters["cog_url"]}'
    source_raster_info = cached_raster_info(source_raster_path)

    warping_kwargs = {}
    try:
        warping_kwargs['target_projection_wkt'] = _epsg_to_wkt(
            parameters["target_epsg"])
        aligned_target_bbox = pygeoprocessing.transform_bounding_box(
            target_bbox, source_raster_info['projection_wkt'],
            warping_kwargs['target_projection_wkt'])
    except KeyError:
        # If we're keeping the same project, just align the requested bounding
        # box to the raster's grid.
        aligned_target_bbox = _align_bbox(target_bbox, source_raster_info)

    try:
        # Make sure pixel sizes are floats.
        target_cellsize = list(map(float, parameters["target_cellsize"]))
    except KeyError:
        target_cellsize = source_raster_info['pixel_size']

    # make sure the target cell's height is negative
    if not target_cellsize[1] < 0:
        target_cellsize[1] *= -1

    try:
        # do the clipping
        target_basename = os.path.splitext(
            os.path.basename(parameters["cog_url"]))[0]
        target_raster_path = os.path.join(
            WORKSPACE_DIR, f'{target_basename}--{uuid.uuid4()}.tif')
        pygeoprocessing.warp_raster(
            source_raster_path, target_cellsize, target_raster_path, 'near',
            target_bb=aligned_target_bbox, **warping_kwargs)
    except Exception:
        app.logger.exception("Failed to warp raster; aborting")
        os.remove(target_raster_path)
        raise

    try:
        filesize = humanize.naturalsize(os.path.getsize(target_raster_path))

        today = datetime.datetime.now().strftime('%Y-%m-%d')
        bucket_filename = f"{today}--{os.path.basename(target_raster_path)}"

        app.logger.info(f"Uploading to bucket: {bucket_filename}")
        bucketname = re.sub('^gs://', '', TARGET_FILE_BUCKET)
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucketname)
        blob = bucket.blob(bucket_filename)
        blob.upload_from_filename(target_raster_path)
    except Exception:
        app.logger.exception("Falling back to cmdline gsutil")
        subprocess.run(["gsutil", "cp", source_raster_path,
                        f'{TARGET_FILE_BUCKET}/{bucket_filename}'])
    finally:
        app.logger.info(f"Deleting local file {target_raster_path}")
        os.remove(target_raster_path)

    downloadable_raster_path = f"{TARGET_BUCKET_URL}/{bucket_filename}"
    app.logger.info("Returning URL: %s", downloadable_raster_path)
    return jsonify({'url': downloadable_raster_path,
                    'size': filesize})


@app.route('/multiclip', methods=['POST'])
def multiclip():
    """Clip several COGs to the same bounding box.

    All parameters are passed as JSON args.

    Args:
        cog_urls (list): A list of URLs of the COGs to clip.  Must be located
            in a trusted bucket.
        target_bbox (list): A list of the target bounding box, in the form
            minx, miny, maxx, maxy.
        target_epsg (str, int): The EPSG code of the clipped raster's target
            projection.
        target_cellsize (list): A 2-tuple of the target cell size of the
            clipped/reprojected COG, in the form (x size, y size).  If ``y
            size`` is not negative, it will be converted to a negative value.

    Returns:
        A JSON response body with the attributes:

            * ``url``: The public URL of a zipfile containing the clipped COGs
            * ``size``: A human-readable string representing the filesize of
                the zipfile.
    """
    pass


@app.route('/status')
def status():
    def _generator():
        try:
            logging_queue = queue.Queue()
            handler = logging.handlers.QueueHandler(logging_queue)
            SOURCE_LOGGER.addHandler(handler)
            while True:
                record = logging_queue.get(block=True)
                if record.msg == 'Done!':
                    break
                yield json.dumps(record.__dict__)
        finally:
            SOURCE_LOGGER.removeHandler(handler)
    return flask.stream_with_context(_generator())


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        'app:app',
        host=os.environ.get('HOST', '127.0.0.1'),
        port=int(os.environ.get('PORT', 8000)),
        log_level='info'
    )
