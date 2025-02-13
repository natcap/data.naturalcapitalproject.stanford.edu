let titiler_url = 'https://titiler-897938321824.us-west1.run.app';
//let server_url = 'http://127.0.0.1:5000'
let server_url = 'https://clipping-service-897938321824.us-west1.run.app'

function setActivitySpinner(enabled) {
  var style;
  if (enabled === "hide") {
    style = "hidden";
  } else {
    style = undefined;
  }
  $('#activity-spinner').css("visibility", style);
}

// Get the query parameters from the URL string.
function getURLParameters() {
  const queryParams = new URLSearchParams(window.location.search);
  const params = {};
  for (const [key, value] of queryParams.entries()) {
    params[key] = value;
  }
  return params;
}

// Load the starting lat/lng from the URL parameters
var queryParams = getURLParameters();
var start_coords;
if (queryParams.lat !== undefined && queryParams.lng !== undefined) {
  try {
    start_coords = [parseFloat(queryParams.lat), parseFloat(queryParams.lng)];
  } catch (error) {
    console.error(`Error parsing lat/lng from query parameters lat:${queryParams.lat}, lng:${queryParams.lng}`);
    console.error(error);
  }
  console.log(`Setting starting coords ${start_coords} from query parameter`);
} else {
  start_coords = [0, 0];  // default to the center of the map
}

// Load the starting zoom level from the URL parameters
var start_zoom;
if (queryParams.zoom !== undefined) {
  start_zoom = parseInt(queryParams.zoom);
  console.log(`Setting starting zoom level ${start_zoom} from query parameter`);
} else {
  start_zoom = 5;
}

if (queryParams.cog_url !== undefined) {
  document.getElementById('cog-url').value = queryParams.cog_url;
  console.log(`Setting COG url from query parameter: ${queryParams.cog_url}`);
  document.getElementById('cog-url-row').classList.add('invisible');
}

var map = L.map('map').setView(start_coords, start_zoom);
L.control.scale().addTo(map);  // add scale to map

// Create a custom ColorBar class
L.Control.ColorBar = L.Control.extend({
  onAdd: function(map) {
    console.log('adding colorbar');
    var ramp_box = L.DomUtil.create('div');
    return ramp_box;
  },
  onRemove: function(map) {
    console.log('removing colorbar');
  },
  updateColors: function(colormap) {
    console.log('updating colors for color map');
    console.log(colormap);
  }
});
L.control.colorbar = function(opts) {
  return new L.Control.ColorBar(opts);
}
var colorbar_control = L.control.colorbar({position: 'topright'}).addTo(map);



function loadTiles() {
  // clear tiles
  map.eachLayer(function (layer) {
    map.removeLayer(layer);
  });

  // load tile layers
  //
  // OSM basemap
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="http://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  }).addTo(map);

  var cog_url = document.getElementById('cog-url').value;
  console.log(`Getting tiles and metadata for ${cog_url}`);
  var prepared_params = "";

  setActivitySpinner('show');
  if (cog_url !== '') {
    fetch(cog_url, {
      method: 'HEAD',
    }).then(response => {
      setActivitySpinner('hide');
      if (response.ok) {
        var params_string = "";
        var percentiles = [2, 20, 40, 60, 80, 98];
        for (const percentile of percentiles) {
          params_string += `&p=${percentile}`;
        }
        var url = `${titiler_url}/cog/statistics?url=${encodeURIComponent(cog_url)}${params_string}`;
        console.log(url);

        setActivitySpinner('show');
        fetch(url).then(stats_resp => {
          setActivitySpinner('hide');
          if (stats_resp.ok) {
            return stats_resp.json();
          } else {
            console.error(stats_resp);
          }
        }).then(stats_json => {
          // from our CKAN plugin
          const colors = [
            [75, 171, 57, 50],  // #4BAB39
            [0, 143, 95, 200],  // #008F5F
            [0, 100, 110, 200], // #00646E
            [28, 58, 109, 200], // #1C3A6D
            [32, 40, 93, 200],  // #20285D
            [39, 0, 59, 200],   // #27003B
          ];
          var colormap = {};
          var raster_min = stats_json['b1']['min'];
          var raster_max = stats_json['b1']['max'];
          percentiles.forEach((percentile, i) => {
            var percentile_value = stats_json['b1'][`percentile_${percentile}`];
            if (percentile_value <= raster_min || i == 0) {
              colormap[0] = colors[0];
            } else if (percentile_value >= raster_max || i == colors.length-1) {
              colormap[255] = colors[colors.length - 1];
            } else {
              var color_index = Math.round(
                ((percentile_value - raster_min) / (raster_max - raster_min)) * 255);
              colormap[color_index] = colors[i];
            }
          });
          colorbar_control.updateColors(colormap);

          const params = {
            tile_scale: 2,
            format: 'webp',
            bidx: 1,
            rescale: `${stats_json['b1']['percentile_2']},${stats_json['b1']['percentile_98']}`,
            colormap: JSON.stringify(colormap),
            colormap_type: 'linear',
          }
          prepared_params = Object.entries(params)
            .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
            .join('&');

          // COG overlay
          var colorized_url = `${titiler_url}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?url=${encodeURIComponent(cog_url)}`;
          if (prepared_params !== "") {
            colorized_url = `${colorized_url}&${prepared_params}`;
          }

          var metadata_url = `${server_url}/metadata?cog_url=${encodeURIComponent(cog_url)}`;
          var attribution;
          fetch(metadata_url).then(metadata_resp => {
            if (metadata_resp.ok) {
              return metadata_resp.json();
            } else {
              console.error(metadata_resp);
            }
          }).then(metadata_json => {
            attribution = metadata_json['contact']['organization'];
            L.tileLayer(
                colorized_url, {
                maxZoom: 19,
                attribution: attribution,
            }).addTo(map);

            // Update the title of the clipper
            document.getElementById('title').textContent = "Clip layer"
            document.getElementById('layer-name').textContent = metadata_json['title'];

          });
        });

        // Get pixel size from COG stats.
        setActivitySpinner('show');
        var cog_info_url = `${server_url}/info?cog_url=${encodeURIComponent(cog_url)}`;
        fetch(cog_info_url).then(response => {
          setActivitySpinner('hide');
          if (response.ok) {
            return response.json()
          } else {
            console.error(response);
          }
        }).then(info_json => {
          document.getElementById('targetEPSG').value = info_json['info']['stac']['proj:epsg'];
          var geotransform = info_json['info']['geoTransform'];
          document.getElementById('targetResolutionX').value = geotransform[1];
          document.getElementById('targetResolutionY').value = geotransform[5];
          updateSRSUnits();
        });

      } else {
        console.log(response);
        console.error('The COG at ' + cog_url + ' did not respond to a HEAD request.');
      }
    });
  }
}
loadTiles();


var areaSelect = L.areaSelect({
  width:document.getElementById('map').offsetWidth/2,
  height:document.getElementById('map').offsetHeight/2,
  //minwidth:40,
  //minheight:40,
  //minHorizontalSpacing:40,
  //minVerticalSpacing:100,
  //keepAspectRatio:false
});
// read the bounds
areaSelect.on("change", function() {
  var bounds = this.getBounds();

  document.getElementById('targetBboxWest').textContent = bounds.getWest();
  document.getElementById('targetBboxSouth').textContent = bounds.getSouth();
  document.getElementById('targetBboxEast').textContent = bounds.getEast();
  document.getElementById('targetBboxNorth').textContent = bounds.getNorth();
});

areaSelect.addTo(map);


async function getInfo() {
  var cog_url = document.getElementById('cog-url').value;
  const url = `${server_url}/info?cog_url=${encodeURIComponent(cog_url)}`;
  try {
    const response = await fetch(url, {
      method: "get",
    });
    if (!response.ok) {
      throw new Error(`Response status: ${response.status}`);
    }
    const json = await response.json();
    console.log(json.info);
  } catch (error) {
    console.error(error.message);
  }
}


async function getData() {
  await delay(5000);  // Give it 5s before checking status
  const url = `${server_url}/status`;
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Response status: ${response.status}`);
    }

    // Calling text() would wait until the whole file downloads.
    //const json = await response.text();
    //console.log(json);

    // Using TextDecoderStream to read the response stream as it arrives.
    // https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch#handling_the_response
    const stream = response.body.pipeThrough(new TextDecoderStream());
    for await (const value of stream) {
      const logrecord = JSON.parse(value);

      if (logrecord.name.startsWith('pygeoprocessing')) {
        const percent_complete = logrecord.message.match(/[0-9]+.?[0-9]+%/g);
        const progress_bar = document.getElementById('clipping-progress-bar');
        if (percent_complete) {
          progress_bar.style.width = percent_complete[0];
          progress_bar.innerHTML = percent_complete[0];
          if (percent_complete[0].match(/100.?0+%/g)) {
            progress_bar.innerHTML = 'Finalizing upload';
          }
        }
      }
    }
  } catch (error) {
    console.error(error.message);
  }
  let progress_bar = document.getElementById('clipping-progress-bar');
  progress_bar.classList.remove('progress-bar-animated');
  progress_bar.classList.add('bg-success');

}

async function clip_cog() {
  var cancel_button = document.getElementById('cancel-button');
    cancel_button.classList.add('visible');
    cancel_button.classList.remove('invisible');

  var submit_button = document.getElementById('submit');
  submit_button.disabled = true;
  submit_button.innerText = "Clipping...";

  var progress_bar = document.getElementById('clipping-progress-bar');
  progress_bar.innerText = 'Starting ...'
  progress_bar.classList.add('progress-bar-animated')
  progress_bar.style.width = '100%';
  try {
    var bounds = areaSelect.getBounds();

    var epsg_code = document.getElementById('targetEPSG').value;
    if (epsg_code == '' || epsg_code == undefined) {
      console.log("EPSG code is empty, defaulting to 4326");
      epsg_code = 4326;
    }

    const clip_response = await fetch(`${server_url}/clip`, {
      method: "POST",
      body: JSON.stringify({
        cog_url: document.getElementById('cog-url').value,
        //target_bbox: [-13.304443, 7.247962, -12.183837999999998, 7.999312000000001],
        //target_bbox: [-33.304443, 7.247962, -12.183837999999998, 27.999312000000001],
        target_bbox: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()],
        target_epsg: epsg_code,
      }),
      headers: {
        "Content-Type": "application/json"
      }
    });
    const clip_json = await clip_response.json();
    // By this point, we have the uploaded file on the cloud
    var download_button = document.getElementById('download');
    download_button.setAttribute('download_url', clip_json.url);
    download_button.classList.remove('invisible');
    download_button.classList.add('visible');
    download_button.innerText = `Download clipped raster (${clip_json.size})`;

    var progress_bar = document.getElementById('clipping-progress-bar');
    progress_bar.classList.remove('progress-bar-animated');
    progress_bar.classList.add('bg-success');
    progress_bar.classList.remove('progress-bar-striped');
    progress_bar.innerText = 'Done!';

    cancel_button.classList.remove('visible');
    cancel_button.classList.add('invisible');

    submit_button.innerText = "Clip raster";
  } catch (error) {
    console.error(error.message);
  }
  submit_button.disabled = false;
  submit_button.innerHtml = "Submit";

}

async function updateSRSUnits() {
  var epsg_code = document.getElementById('targetEPSG').value;
  console.log(`Updating SRS info for ${epsg_code}`);
  const epsg_response = await fetch(`${server_url}/epsg_info?epsg_code=${epsg_code}`, {
    method: "GET",
  });
  if (!epsg_response.ok) {
    throw new Error(`Response status: ${epsg_response.status}`);
  }
  const json = await epsg_response.json();
  if (json['status'] == 'success') {
    document.getElementById('targetEPSGInfo').textContent = json['epsg_name'];
    document.getElementById('targetResolutionXLabel').textContent = `Units: ${json['srs_units']}`;
    document.getElementById('targetResolutionYLabel').textContent = `Units: ${json['srs_units']}`;
  }
}

function download() {
  var download_button = document.getElementById('download');
  var download_url = download_button.getAttribute('download_url');
  window.open(download_url, '_blank');
}

function updateBounds() {
  areaSelect.setBounds([
    {lat: parseFloat(document.getElementById('targetBboxSouth').value),
      lng: parseFloat(document.getElementById('targetBboxWest').value)},
    {lat: parseFloat(document.getElementById('targetBboxNorth').value),
      lng: parseFloat(document.getElementById('targetBboxEast').value)}
  ]);
}
