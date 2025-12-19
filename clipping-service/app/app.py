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
import shapely.prepared
import shapely.wkb
import yaml
from flask import jsonify
from flask import request
from flask_cors import CORS
from google.cloud import storage
from osgeo import gdal
from osgeo import ogr
from osgeo import osr

app = flask.Flask(__name__, template_folder='templates')

cors_origins = [
    'https://data-staging.naturalcapitalproject.org',
    'https://data.naturalcapitalproject.stanford.edu'
]

if os.environ.get("DEV_MODE"):
    cors_origins.append("https://localhost:*")

CORS(app, resources={
    '/*': {
        'origins': cors_origins
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
TARGET_BUCKET_SUBDIR = 'clipped'
TARGET_BUCKET_URL = f'{GOOGLE_STORAGE_URL}/jupyter-app-temp-storage/{TARGET_BUCKET_SUBDIR}'
WORKSPACE_DIR = os.environ.get('WORKSPACE_DIR', os.getcwd())
app.logger.info("WORKSPACE_DIR: %s", WORKSPACE_DIR)
pygeoprocessing.geoprocessing._LOGGING_PERIOD = 1.0

RASTER = 'raster'
VECTOR = 'vector'


def _epsg_to_wkt(epsg_code):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(epsg_code))
    return srs.ExportToWkt()


def _clip_vector_to_bounding_box(
        source_vector_path, target_bounding_box, target_vector_path,
        target_projection_wkt=None):
    """Clip a vector to the intersection of a target bounding box.

    Optionally also reproject the vector.

    Args:
        source_vector_path (str): path to a FlatGeobuf to be clipped.
        target_bounding_box (list): list of the form [xmin, ymin, xmax, ymax]
        target_vector_path (str): path to a FlatGeobuf to store the clipped
            vector.
        target_projection_wkt=None (str): target projection in wkt. Can be
            none to indicate no reprojection is required.

    Returns:
        None
    """
    shapely_mask = shapely.prepared.prep(
        shapely.geometry.box(*target_bounding_box))

    app.logger.debug("Opening base vector...")
    base_vector = gdal.OpenEx(source_vector_path, gdal.OF_VECTOR)
    base_layer = base_vector.GetLayer()
    base_layer.SetSpatialFilterRect(*target_bounding_box)
    base_layer_defn = base_layer.GetLayerDefn()
    base_geom_type = base_layer.GetGeomType()
    base_srs = base_layer.GetSpatialRef()

    if target_projection_wkt is not None:
        target_srs = osr.SpatialReference()
        target_srs.ImportFromWkt(target_projection_wkt)
        coord_trans = osr.CreateCoordinateTransformation(base_srs, target_srs)
    else:
        target_srs = base_srs

    app.logger.debug("Setting up target...")
    target_driver = gdal.GetDriverByName('FlatGeobuf')
    target_vector = target_driver.Create(
        target_vector_path, 0, 0, 0, gdal.GDT_Unknown)
    target_layer = target_vector.CreateLayer(
        base_layer_defn.GetName(), target_srs, base_geom_type)
    target_layer.CreateFields(base_layer.schema)

    app.logger.debug("Clipping vector...")
    target_layer.StartTransaction()
    invalid_feature_count = 0
    n_processed = 0
    last_log_msg_time = time.time()
    for feature in base_layer:
        now = time.time()
        if now >= last_log_msg_time+2.0:
            app.logger.debug(f"Processed {n_processed} features so far")
            last_log_msg_time = now
        n_processed += 1

        invalid = False
        geometry = feature.GetGeometryRef()
        try:
            shapely_geom = shapely.wkb.loads(bytes(geometry.ExportToWkb()))
        # Catch invalid geometries that cannot be loaded by Shapely;
        # e.g. polygons with too few points for their type
        except shapely.errors.ShapelyError:
            invalid = True
        else:
            if shapely_geom.is_valid:
                # Check for intersection rather than use gdal.Layer.Clip()
                # to preserve the shape of the polygons
                if shapely_mask.intersects(shapely_geom):
                    # This appears to use the network WAY less
                    new_feature = ogr.Feature.Clone(feature)

                    # If we need to, transform the geometry
                    if target_projection_wkt is not None:
                        geometry.Transform(coord_trans)
                        new_feature.SetGeometry(geometry)

                    target_layer.CreateFeature(new_feature)
            else:
                invalid = True
        finally:
            if invalid:
                invalid_feature_count += 1
                app.logger.warning(
                    f"The geometry at feature {feature.GetFID()} is invalid "
                    "and will be skipped.")

    target_layer.CommitTransaction()

    if invalid_feature_count:
        app.logger.warning(
            f"{invalid_feature_count} features in {source_vector_path} "
            "were found to be invalid during clipping and were skipped.")

    base_layer = None
    base_vector = None
    target_layer = None
    target_vector = None


@functools.lru_cache
def cached_file_info(vsi_file_path, file_type):
    app.logger.info(f"Getting file info for {file_type} at {vsi_file_path}")
    if file_type == RASTER:
        try:
            return pygeoprocessing.get_raster_info(vsi_file_path)
        except Exception:
            app.logger.error("Failed to read raster info for %s", vsi_file_path)
            raise
    elif file_type == VECTOR:
        try:
            return pygeoprocessing.get_vector_info(vsi_file_path)
        except Exception:
            app.logger.error("Failed to read vector info for %s", vsi_file_path)
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
    file_url = request.args.get('file_url')

    yaml_data = yaml.load(
        requests.get(f'{file_url}.yml', stream=True).content,
        Loader=yaml.Loader)
    return yaml_data


@app.route('/info', methods=['GET'])
@functools.lru_cache
def info():
    file_url = request.args.get("file_url")
    result = gdal.Info(f'/vsicurl/{file_url}', options=['-json'])

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
    parameters = request.get_json()
    app.logger.info(parameters)

    if not parameters['file_url'].startswith(TRUSTED_BUCKET):
        app.logger.error("Invalid source file, not in the known bucket: %s",
                         parameters['file_url'])
        raise ValueError("Invalid source file provided.")

    source_file_type = parameters['layer_type']
    if source_file_type not in [RASTER, VECTOR]:
        raise ValueError("Invalid file type.")

    target_bbox = parameters["target_bbox"]

    # align the bounding box
    source_file_path = f'/vsicurl/{parameters["file_url"]}'
    if source_file_type == VECTOR:
        # source_file_path originates in `mappreview` extra; currently always .mvt,
        # but leaving this flexible in case we support .geojson again in the future
        source_file_path = os.path.splitext(source_file_path)[0] + '.fgb'

    source_file_info = cached_file_info(source_file_path, source_file_type)

    if source_file_type == RASTER:
        app.logger.info("CLIPPING RASTER...")
        warping_kwargs = {}
        try:
            warping_kwargs['target_projection_wkt'] = _epsg_to_wkt(
                parameters["target_epsg"])
            aligned_target_bbox = pygeoprocessing.transform_bounding_box(
                target_bbox, source_file_info['projection_wkt'],
                warping_kwargs['target_projection_wkt'])
        except KeyError:
            # If we're keeping the same projection, just align the requested bounding
            # box to the raster's grid.
            aligned_target_bbox = _align_bbox(target_bbox, source_file_info)

        try:
            # Make sure pixel sizes are floats.
            target_cellsize = list(map(float, parameters["target_cellsize"]))
        except KeyError:
            target_cellsize = source_file_info['pixel_size']

        # make sure the target cell's height is negative
        if not target_cellsize[1] < 0:
            target_cellsize[1] *= -1

        try:
            # do the clipping
            target_basename = os.path.splitext(
                os.path.basename(parameters["file_url"]))[0]
            target_file_path = os.path.join(
                WORKSPACE_DIR, f'{target_basename}--{uuid.uuid4()}.tif')
            pygeoprocessing.warp_raster(
                source_file_path, target_cellsize, target_file_path, 'near',
                target_bb=aligned_target_bbox, **warping_kwargs)
        except Exception:
            app.logger.exception("Failed to warp raster; aborting")
            os.remove(target_file_path)
            raise

    elif source_file_type == VECTOR:
        app.logger.info("CLIPPING VECTOR...")
        try:
            target_projection_wkt = _epsg_to_wkt(
                parameters["target_epsg"])
        except KeyError:
            target_projection_wkt = None

        try:
            # do the clipping
            target_basename = os.path.splitext(
                os.path.basename(parameters["file_url"]))[0]
            target_file_path = os.path.join(
                WORKSPACE_DIR, f'{target_basename}--{uuid.uuid4()}.fgb')
            _clip_vector_to_bounding_box(
                source_file_path, target_bbox, target_file_path,
                target_projection_wkt)
        except Exception:
            app.logger.exception("Failed to clip vector; aborting")
            os.remove(target_file_path)
            raise

    try:
        filesize = humanize.naturalsize(os.path.getsize(target_file_path))

        today = datetime.datetime.now().strftime('%Y-%m-%d')
        bucket_filename = f"{today}--{os.path.basename(target_file_path)}"

        app.logger.info(f"Uploading to bucket: {bucket_filename}")
        bucketname = re.sub('^gs://', '', TARGET_FILE_BUCKET)
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucketname)
        blob = bucket.blob(f"{TARGET_BUCKET_SUBDIR}/{bucket_filename}")
        blob.upload_from_filename(target_file_path)
    except Exception:
        app.logger.exception("Falling back to cmdline gsutil")
        subprocess.run(["gsutil", "cp", source_file_path,
                        f'{TARGET_FILE_BUCKET}/{bucket_filename}'])
    finally:
        app.logger.info(f"Deleting local file {target_file_path}")
        os.remove(target_file_path)

    downloadable_raster_path = f"{TARGET_BUCKET_URL}/{bucket_filename}"
    app.logger.info("Returning URL: %s", downloadable_raster_path)
    return jsonify({'url': downloadable_raster_path,
                    'size': filesize})


@app.route("/start")
def submit():
    for i in range(10):
        time.sleep(1)
        print(f"logging progress {i*10}%")
        SOURCE_LOGGER.info(f"Progress: {i*10}%")
    SOURCE_LOGGER.info("Done!")
    return 'Done!'


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
