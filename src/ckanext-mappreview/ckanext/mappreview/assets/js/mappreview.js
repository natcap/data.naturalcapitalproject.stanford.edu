ckan.module("mappreview", function ($, _) {
  "use strict";
  return {
    options: {
      config: {},
      globalConfig: {},
      debug: false,
    },

    vectorColors: [
      '#E04F39',
      '#F9A44A',
      '#FEC51D',
      '#A57DAE',
    ],

    /**
     * Linear scale pixel values to [0, 255] range
     */
    _getColorMapValue: function (min, max, value) {
      if (value <= min) return 0;
      if (value >= max) return 255;
      return Math.round(((value - min) / (max - min)) * 255);
    },

    _getGlobalConfig: function () {
      return JSON.parse(this.options.globalConfig.replace(/'/g, '"'));
    },

    _getRasterPoint: async function (layer, lngLat) {
      const response = await fetch(`${this._getGlobalConfig().titiler_url}/cog/point/${lngLat.lng},${lngLat.lat}?url=${encodeURIComponent(layer.url)}`);
      return (await response.json());
    },

    _getRasterTilejsonUrl: function (layer) {
      const base = this._getGlobalConfig().titiler_url;
      const endpoint = '/cog/WebMercatorQuad/tilejson.json';

      const colors = [
        [75, 171, 57, 50], // #4BAB39
        [0, 143, 95, 255], // #008F5F
        [0, 100, 110, 255], // #00646E
        [28, 58, 109, 255], // #1C3A6D
        [32, 40, 93, 255], // #20285D
        [39, 0, 59, 255], // #27003B
      ];

      const colormap = {};

      const percentiles = [2, 20, 40, 60, 80, 98];
      percentiles.forEach((percentile, i) => {
        let colorIndex = this._getColorMapValue(layer.pixel_min_value, layer.pixel_max_value, layer[`pixel_percentile_${percentile}`]);

        // Color map must start with 0 and end with 255
        if (i === 0) colorIndex = 0;
        if (i === percentiles.length - 1) colorIndex = 255;

        colormap[colorIndex] = colors[i];
      });

      const params = {
        tile_scale: 2,
        url: layer.url,
        bidx: 1,
        format: 'webp',
        rescale: `${layer.pixel_percentile_2},${layer.pixel_percentile_98}`,
        colormap: JSON.stringify(colormap),
        colormap_type: 'linear',
      };

      const paramsPrepared = Object.entries(params)
        .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
        .join('&');

      return `${base}${endpoint}?${paramsPrepared}`;
    },

    _getRasterLayer: function (layer) {
      return {
        id: layer.name,
        type: 'raster',
        source: layer.name,
        paint: {
          'raster-opacity': ['interpolate', ['linear'], ['zoom'], 0, 0.75, 12, 1],
        },
      };
    },

    _getVectorLayers: function (layer, index) {
      const color = this.vectorColors[index % this.vectorColors.length];

      if (layer.vector_type === 'Point') {
        return {
          id: layer.name,
          type: 'circle',
          source: layer.name,
          paint: {
            'circle-radius': 5,
            'circle-color': color,
          }
        };
      }
      else if (layer.vector_type === 'Line') {
        return {
          id: layer.name,
          type: 'line',
          source: layer.name,
          paint: {
            'line-width': 5,
            'line-color': color,
          }
        };
      }
      else if (layer.vector_type === 'Polygon') {
        return [
          {
            id: `${layer.name}-outline`,
            type: 'line',
            source: layer.name,
            paint: {
              'line-width': 2,
              'line-color': color,
            }
          },
          {
            id: layer.name,
            type: 'fill',
            source: layer.name,
            paint: {
              'fill-color': color,
              'fill-opacity': 0.5
            }
          }
        ];
      }
    },

    initialize: function () {
      jQuery.proxyAll(this, '_getGlobalConfig');
      jQuery.proxyAll(this, '_getRasterLayer');
      jQuery.proxyAll(this, '_getRasterTilejsonUrl');
      jQuery.proxyAll(this, '_getRasterPoint');
      jQuery.proxyAll(this, '_getVectorLayers');

      const config = JSON.parse(this.options.config.replace(/'/g, '"'));
      const globalConfig = this._getGlobalConfig();

      mapboxgl.accessToken = globalConfig.mapbox_api_key;
      const map = new mapboxgl.Map({
        container: 'map',
        style: globalConfig.mapbox_style,
        bounds: config.map.bounds,
        zoom: config.map.minzoom + 2,
        minZoom: config.map.minzoom,
        maxZoom: config.map.maxzoom,
      });

      const sources = config.layers.map(l => {
        if (l.type === 'raster') {
          const url = this._getRasterTilejsonUrl(l);
          return {
            id: l.name,
            type: 'raster',
            url,
          };
        }
        else if (l.type === 'vector') {
          return {
            id: l.name,
            type: 'geojson',
            data: l.url,
          };
        }
        else {
          console.warn(`Unsupported source type: ${l.type}`);
          return null;
        }
      });

      const layers = config.layers
        .map((l, i) => {
          if (l.type === 'raster') {
            return this._getRasterLayer(l);
          }
          else if (l.type === 'vector') {
            return this._getVectorLayers(l, i);
          }
          else {
            console.warn(`Unsupported layer type: ${l.type}`);
            return null;
          }
        })
        .filter(l => l !== null)
        .flat()
        .toSorted((a, b) => {
          const order = ['raster', 'fill', 'line', 'circle'];
          return order.indexOf(a.type) - order.indexOf(b.type);
        });

      map.on('load', () => {
        sources.forEach((source) => {
          // Avoid warning about id
          const cleanSource = Object.fromEntries(Object.entries(source).filter(([k, v]) => k !== 'id'));
          map.addSource(source.id, cleanSource);
        });

        layers.forEach((layer) => {
          map.addLayer(layer);
        });

        const targets = Object.fromEntries(config.layers.map(l => [l.name, l.name]));

        map.addControl(new MapboxLegendControl(targets, {
          showDefault: true,
          showCheckbox: true,
          onlyRendered: false,
          reverseOrder: true
        }), 'top-right');
      });

      // Move event is triggered for a variety of interactions, so it's the
      // most flexible way to update our coordinates
      //
      // CKAN doesn't want us to have multiple plugin assets interacting with
      // one another, so updating DOM attributes allows us to use the DOM as a
      // way to pass data between parts of the page.
      for (const eventname of ['move', 'load']) {
        map.on(eventname, () => {
          const center = map.getCenter();
          $('div.mappreview').attr({
            "center-lat": center.lat,
            "center-lng": center.lng,
            "zoom": map.getZoom(),
          });
        });
      }

      map.on('click', async (e) => {
        let popupContent;

        const vectorLayers = layers.filter(s => s.type !== 'raster');
        if (vectorLayers.length > 0) {
          const vectorFeature = map.queryRenderedFeatures(e.point, { layers: vectorLayers.map(l => l.id) })[0];

          if (vectorFeature) {
            const rows = Object.entries(vectorFeature.properties)
              .map(([key, value]) => `
                <div class="popup-key">${key}</div>
                <div class="popup-value">${value}</div>
              `);

            popupContent = `<h3>${config.layers[0].name}</h3>
              <div class="popup-grid">
                ${rows.join('')}
              </div>`;
          }
        }

        if (!popupContent && (config.layers.length === 1 && config.layers[0].type === 'raster')) {
          const point = await this._getRasterPoint(config.layers[0], e.lngLat);
          if (!point) return;

          popupContent = `<h3>${config.layers[0].name}</h3>
            <div class="popup-grid">
                <div class="popup-key">value</div>
                <div class="popup-value">${point.values[0]}</div>
            </div>`;
        }

        if (popupContent) {
          const popup = new mapboxgl.Popup({ className: 'mappreview-mapboxgl-popup' })
            .setLngLat(e.lngLat)
            .setMaxWidth("300px")
            .setHTML(popupContent)
            .addTo(map);
        }
      });


      /**********************************************************************
       * Bounding Box Clipping Logic
       *
       * The stuff below this provides everything needed to do clipping on the
       * Mapbox GL JS globe.  We are just using vanilla Mapbox GL JS because it
       * turned out that we could do this with some pretty basic event handling
       * on the Mapbox GL JS globe.
       */

      // These three attributes represent the application state needed.
      const bbox_geojson = {
        type: 'FeatureCollection',
        features: [
          _createGeometryFromBbox(0, 0, 0, 0),
        ],
      };
      var current_vertex = undefined;
      var box_move_origin = undefined;
      const clip_button_id = 'natcapClipModeStart';
      const legend_former_check_state = {}
      const clipping_control_id = 'natcapClippingControl';
      const clip_start_progress_modal_id = 'natcapClipStartProgressModal';
      const clip_mode_cancel = 'natcapClipModeCancel';

      /* Translate the bounding box by some known diff
       *
       * lat_diff (float): The amount in the x direction to shift the bbox
       * lng_diff (float): The amount in the y direction to shift the bbox
       */
      function _translateBbox(lat_diff, lng_diff) {
          var coords = bbox_geojson.features[0].geometry.coordinates[0];
          for (var i=0; i < 5; i++) {
              coords[i][0] += lng_diff;
              coords[i][1] += lat_diff;
          }
      }

      function _createGeometryFromBbox(minx, maxx, miny, maxy) {
          return {
              type: 'Feature',
              geometry: {
                  type: "Polygon",
                  coordinates: [[
                      [minx, miny],
                      [minx, maxy],
                      [maxx, maxy],
                      [maxx, miny],
                      [minx, miny]
                  ]],
              }
          }
      }

      function _updateBboxFromGT(minx, maxx, miny, maxy) {
          console.log(minx, maxx, miny, maxy);
          bbox_geojson.features[0] = _createGeometryFromBbox(minx, maxx, miny, maxy);
      }

      function _vertices() {
          var minx = bbox_geojson.features[0].geometry.coordinates[0][0][0];
          var maxx = bbox_geojson.features[0].geometry.coordinates[0][2][0];
          var miny = bbox_geojson.features[0].geometry.coordinates[0][0][1];
          var maxy = bbox_geojson.features[0].geometry.coordinates[0][2][1];
          return {
              type: 'FeatureCollection',
              features: [
                  {type: 'Feature', properties: {loc: "sw"}, geometry: {type: "Point", coordinates: [minx, miny]}},
                  {type: 'Feature', properties: {loc: "nw"}, geometry: {type: "Point", coordinates: [minx, maxy]}},
                  {type: 'Feature', properties: {loc: "se"}, geometry: {type: "Point", coordinates: [maxx, miny]}},
                  {type: 'Feature', properties: {loc: "ne"}, geometry: {type: "Point", coordinates: [maxx, maxy]}},
              ],
          }
      }

      // Given a new vertex, update the geometry's relevant vertices
      function _updateVertex(loc, lat, lng) {
          var coords = bbox_geojson.features[0].geometry.coordinates[0];
          if (loc == "sw") {
              // this location has 2 points to update, plus neighbors.
              coords[0][0] = lng;
              coords[0][1] = lat;
              coords[4][0] = lng;
              coords[4][1] = lat;

              // update neighbors
              coords[1][0] = lng;
              coords[3][1] = lat;

          } else if (loc == "nw") {
              coords[1][0] = lng;
              coords[1][1] = lat;

              //update neighbors
              coords[0][0] = lng;
              coords[4][0] = lng;
              coords[2][1] = lat;

          } else if (loc == "ne") {
              coords[2][0] = lng;
              coords[2][1] = lat;

              // update neighbors
              coords[1][1] = lat;
              coords[3][0] = lng;

          } else if (loc == "se") {
              coords[3][0] = lng;
              coords[3][1] = lat;

              // update neighbors
              coords[2][0] = lng;
              coords[0][1] = lat;
              coords[4][1] = lat;
          }
      }

      const canvas = map.getCanvasContainer();

      function onVertexMove(e) {

          const coords = e.lngLat.toBounds();

          // set UI indicator for dragging
          canvas.style.cursor = 'grabbing';

          // update the feature in the geojson object
          // and call setData to the source layer geometry on it.
          const bounds = e.lngLat.toBounds();
          _updateVertex(current_vertex, bounds.getNorth(), bounds.getEast());

          map.getSource('bbox').setData(bbox_geojson);
          map.getSource('vertices').setData(_vertices());
      }

      function onVertexMouseUp(e) {
          const coords = e.lngLat;

          current_vertex = undefined;

          // unbind mouse/touch events
          map.off('mousemove', onVertexMove);
          map.off('touchmove', onVertexMove);
      }

      function onBoxMove(e) {
          if (box_move_origin === undefined) {
              return;
          }

          const coords = e.lngLat.toBounds();
          var lat_diff = coords.getNorth() - box_move_origin.getNorth();
          var lng_diff = coords.getWest() - box_move_origin.getWest();

          _translateBbox(lat_diff, lng_diff);
          box_move_origin = coords;
          map.getSource('bbox').setData(bbox_geojson);
          map.getSource('vertices').setData(_vertices());
      }

      function onBoxMouseUp(e) {
          box_move_origin = undefined;

          // unbind mouse/touch events
          map.off('mousemove', onVertexMove);
          map.off('touchmove', onVertexMove);
      }

      function initBoundingBox() {
          // on map startup, set bbox to 1/3 height and width of the viewport, centered at the centerpoint
          // 1/3 was picked through trial and error.
          var center = map.getCenter();
          var viewport = map.getBounds();
          _updateBboxFromGT(
              center.lng - (center.lng - viewport.getWest())/3,  // minx
              center.lng - (center.lng - viewport.getEast())/3,  // maxx
              center.lat - (center.lat - viewport.getSouth())/3, // miny
              center.lat - (center.lat - viewport.getNorth())/3  // maxy
          )
          if (map.getSource('bbox') === undefined) {
            map.addSource('bbox', {type: 'geojson', data: bbox_geojson});
          }
          if (map.getSource('vertices') === undefined) {
            map.addSource('vertices', {type: 'geojson', data: _vertices()});
          }
          map.addLayer({
              id: 'bbox-fill',
              type: 'fill',
              source: 'bbox',
              filter: ["all", ["==", "$type", "Polygon"]],
              paint: {
                "fill-color": "#FFFFFF",
                "fill-outline-color": "#2E2D29",  // Process Black, from NatCap style guide
                "fill-opacity": 0.5,
              },
          });
          map.addLayer({
              "id": "bbox-border",
              "source": 'bbox',
              "type": "line",
              "filter": ["all", ["==", "$type", "Polygon"]],
              "layout": {
                  "line-cap": "round",
                  "line-join": "round",
              },
              "paint": {
                  "line-color": "#2E2D29",
                  "line-dasharray": [0.2, 2],
                  "line-width": 2,
              },
          });
          map.addLayer({
              "id": "vertices-dot",
              "source": "vertices",
              "type": "circle",
              "filter": ["all",
                  ["==", "$type", "Point"],
              ],
              "paint": {
                  "circle-radius": 7,
                  "circle-color": "#2E2D29",
              },
          });
          map.addLayer({
              "id": "vertices-border",
              "source": "vertices",
              "type": "circle",
              "filter": ["all",
                  ["==", "$type", "Point"],
              ],
              "paint": {
                  "circle-radius": 5,
                  "circle-color": "#FFF",
              },
          });

          // Commenting out zoom because this will take a little thought to get
          // this plus the bbox default size stuff working in a nice way.
          //
          // zoom in to the bounding box
          //const bounds = new mapboxgl.LngLatBounds(map.getCenter(), map.getCenter());
          //for (const coord of bbox_geojson.features[0].geometry.coordinates[0]) {
          //  bounds.extend(coord);
          //}
          //map.fitBounds(bounds, {padding: 50, easing: true});
      }

      function hideBoundingBox() {
          // Need to remove the layers before we remove the sources.
          // Otherwise we get an error in the console.
          for (const layer of ['bbox-fill', 'bbox-border', 'vertices-dot', 'vertices-border']) {
              map.removeLayer(layer);
          }

          for (const source of ['bbox', 'vertices']) {
              map.removeSource(source);
          }
      }

      // the `load` event is fired after the first visually complete
      // rendering of the map has occurred.
      map.on('load', () => {
          map.on('mouseenter', 'vertices-dot', () => {
              canvas.style.cursor = 'move';
          });

          map.on('mouseleave', 'vertices-dot', () => {
              canvas.style.cursor = '';
          });

          map.on('mousedown', 'vertices-dot', (e) => {
              // prevent default map behavior
              e.preventDefault();

              // note which vertex we have clicked on
              current_vertex = e.features[0].properties.loc;
              canvas.style.cursor = 'grab';

              map.on('mousemove', onVertexMove);
              map.once('mouseup', onVertexMouseUp);
          });

          map.on('touchstart', 'vertices-dot', (e) => {
              // prevent default map behavior
              e.preventDefault();

              // note which vertex we have clicked on
              current_vertex = e.features[0].properties.loc;
              canvas.style.cursor = 'grab';

              map.on('touchmove', onVertexMove);
              map.once('touchend', onVertexMouseUp);
          });

          map.on('mouseenter', 'bbox-fill', (e) => {
              canvas.style.cursor = 'move';
          });

          map.on('mouseleave', 'bbox-fill', (e) => {
              canvas.style.cursor = '';
          });

          map.on('mousedown', 'bbox-fill', (e) => {
              // prevent default map behavior
              e.preventDefault();
              box_move_origin = e.lngLat.toBounds();
              map.on('mousemove', onBoxMove);
              map.once('mouseup', onBoxMouseUp);
          });
      });

      // clip the layer
      function natcapClipLayer(layer_name) {
        console.log(`Calling natcapClipLayer with ${layer_name}`);
        var layer_details = undefined;
        for (var layer_index in config.layers) {
          var layer = config.layers[layer_index];
          if (layer.name == layer_name) {
            layer_details = layer;
            break;
          }
        }
        console.log(layer_details);

        // hide the "Clip This Layer" button
        document.getElementById(clip_button_id).classList.add('d-none');  // use classList.remove('d-none') to re-enable

        // Uncheck layers that aren't this one.
        // Track which layers are checked for when we exit clipping mode.
        var legend_inputs = document.querySelectorAll('div.mapboxgl-legend-list table.legend-table input');
        for (const legend_input of legend_inputs) {
          legend_former_check_state[legend_input.name] = legend_input.checked;
          if (legend_input.name !== layer.name) {
            legend_input.checked = false;
          } else {
            legend_input.checked = true;
          }
        }

        // Add the bounding box
        initBoundingBox();

        // enable the progress modal trigger button
        document.getElementById(clip_start_progress_modal_id).classList.remove('d-none');
        document.getElementById(clip_mode_cancel).classList.remove('d-none');


        // Next steps
        // X Hide the "Clip this layer" button
        // X Uncheck layers that aren't this one
        // X Add the bounding box
        // * Add a bootstrap PRIMARY button to start clipping ("Clip to this extent")
        //   * When clipping is started, we should pop up a modal dialog with config options
        //   * Modal stays open when clipping
        // * Add a bootstrap secondary button (or some other way) to cancel clipping ("Cancel clipping")
        // * When clipping is cancelled, hide the clipping-mode buttons and show the "Clip this layer" button again.

        console.log('finished natcapClipLayer');
      }

      function natcapClipLayerCancel() {
        document.getElementById(clip_button_id).classList.remove('d-none');
        document.getElementById(clip_start_progress_modal_id).classList.add('d-none');
        document.getElementById(clip_mode_cancel).classList.add('d-none');
        hideBoundingBox();
      }


      var selected_layer;
      class ClippingControl{
        onAdd(map) {

            // config.layers attributes relevant to us
            // name - filename (e.g. awc.tif)
            // type - "raster"
            // url - the clipping url

            this._map = map;
            this._container = document.createElement('div');
            this._container.id = clipping_control_id;
            this._container.className = 'mapboxgl-ctrl';
            this._container.textContent = 'Hello, world';

            // append hidden buttons to the innerHTML, to be enabled when clipping mode starts.
            var progress_modal_trigger_button = `
              <div class="btn-group" role="group">
                <button class="btn btn-primary d-none"
                        type="button"
                        data-bs-toggle="modal"
                        data-bs-target="#natcapClipProgressModal"
                        id="${clip_start_progress_modal_id}">
                  <i class="fa-solid fa-check"></i>
                  Clip to this bounding box
                </button>
                <button type="button"
                        id='${clip_mode_cancel}'
                        class="btn btn-secondary d-none">
                  Cancel
                  <i class="fa-solid fa-xmark"></i>
                </button>
              </div>`

            var rasters = [];
            for (const layer of config.layers) {
              if (layer.type === "raster") {
                rasters.push(layer);
              }
            }
            if (rasters.length == 0) {
              this._container.innerHTML = `
                <button type="button"
                        class="btn btn-outline-secondary"
                        disabled
                        title="At this time, clipping only works with raster layers.">
                  Clipping is disabled
                </button>`;
            } else if (rasters.length == 1) {
              this._container.innerHTML = `
                <button type="button"
                        class="btn btn-secondary"
                        id='${clip_button_id}'>
                  <i class="fa-solid fa-scissors"></i>
                  Clip this layer
                </button>${progress_modal_trigger_button}`;

              this._container.getElementsByTagName('button')[0].addEventListener('click', function() {
                console.log('single-raster button click handler');
                console.log(rasters[0]);
                // when the 'clip to this bounding box' button is selected, set an attribute of the button
                document.getElementById(clip_button_id).setAttribute(
                  'layer-name', rasters[0].name);
                document.getElementById(clip_button_id).setAttribute(
                  'layer-url', rasters[0].url);
                document.getElementById(clip_button_id).setAttribute(
                  'layer-type', rasters[0].type);
                selected_layer = rasters[0].name;
                natcapClipLayer(rasters[0].name);
              });
            } else {
              var raster_string = "";
              for (const raster_layer of rasters) {
                raster_string += `
                  <li>
                    <a class="dropdown-item
                       href="#"
                       layer-name='${raster_layer.name}'
                       layer-url=${raster_layer.url}>
                      ${raster_layer.name}
                    </a>
                  </li>\n`
              }
              this._container.innerHTML = `
                <div class="dropup">
                  <button class='btn btn-secondary dropdown-toggle'
                          data-bs-toggle='dropdown'
                          aria-expanded='false'
                          id='${clip_button_id}'>
                    <i class="fa-solid fa-scissors"></i>
                    Clip this layer
                  </button>
                  <ul class="dropdown-menu">
                    ${raster_string}
                  </ul>
                </div>
                ${progress_modal_trigger_button}
              `;
              for (const elem of this._container.getElementsByTagName('a')) {
                elem.addEventListener('click', function() {
                  // When clicked, note the selected layer in the modal.
                  document.getElementById(clip_button_id).setAttribute(
                    'layer-name', elem.getAttribute('layer-name'));
                  document.getElementById(clip_button_id).setAttribute(
                    'layer-url', elem.getAttribute('layer-url'));
                  document.getElementById(clip_button_id).setAttribute(
                    'layer-type', elem.getAttribute('layer-type'));
                });
              }
            }

            // add a handler for cancelling clipping mode.
            // The button _should_ be in a known order, but we can just find
            // all the possible buttons that might match and then just add the
            // handler to the right one.
            for (const btn of this._container.getElementsByTagName('button')) {
              if (btn.id === clip_mode_cancel) {
                btn.addEventListener('click', function() {
                  console.log('cancelling clip mode');
                  natcapClipLayerCancel();
                });
              }
            }
            return this._container;
        }

        _toggleClippingOptions() {
            if ($('#clipOptions').hasClass('show')) {
                hideBoundingBox();
            } else {
                initBoundingBox();
            }
            $('#clipOptions').collapse('toggle');
        }

        onRemove() {
            this._container.parentNode.removeChild(this._container);
            this._map = undefined;
        }
      }
      map.addControl(new ClippingControl(), 'bottom-right');

      /**  Modal Controls
        */
      function downloadComplete(download_url, filesize) {
          document.getElementById('clipping-progress').classList.add('d-none');
          document.getElementById('natcap-clip-cancel-button').classList.add('d-none');
          document.getElementById('natcap-clip-done-button').classList.remove('d-none');
          document.getElementById('download-size').innerText = filesize;
          document.getElementById('natcapClipDownloadClippedLayer').classList.remove('d-none');
          document.getElementById('natcapClipDownloadClippedLayer').setAttribute(
            'onclick', `window.open('${download_url}')`);
          document.getElementById('natcapClipInProgress').classList.add('d-none');
      }

      function submitForm() {
          console.log('form submitted');
          document.getElementById('clipping-progress').classList.remove('d-none');
          document.getElementById('natcap-clip-cancel-button').classList.remove('d-none');
          document.getElementById('natcap-clip-submit-button').classList.add('d-none');
          document.getElementById('natcapClipInProgress').classList.remove('d-none');
          document.getElementById('natcapClipInitOptions').classList.add('d-none');

          var target_cog = document.getElementById(clip_button_id).getAttribute('layer-url');
          var clipping_options = {
            cog_url: target_cog,
          }

          // are we overriding the EPSG code?  If not, don't include it in the clipping_options.
          var epsg_override_checkbox = document.getElementById('natcapClipSettingOverrideEPSG');
          if (epsg_override_checkbox.checked) {
            var target_epsg = document.getElementById('natcapClipSettingEPSGCode').value;
            clipping_options['target_epsg'] = target_epsg;
          }

          // are we overriding the pixel size?  If not, don't include it in the clipping options.
          var pixelsize_override_checkbox = document.getElementById('natcapClipSettingOverridePixelSize');
          if (pixelsize_override_checkbox.checked) {
            var target_pixel_size = document.getElementById('natcapClipSettingPixelSize').value;
            clipping_options['target_cellsize'] = [target_pixel_size, -target_pixel_size];
          }

          console.log(clipping_options);

          const clipping_service_url = 'https://clipping-service-897938321824.us-west1.run.app';
          fetch(`${clipping_service_url}/clip`, {
            method: "POST",
            body: JSON.stringify(clipping_options),
            headers: {
              "Content-Type": "application/json",
            },
          }).then(response => {
            if (response.ok) {
              return response.json();
            } else {
              console.error("Something went wrong while clipping");
              console.error(response);
            }
          }).then(response_json => {
            downloadComplete(response_json.url, response_json.size);
          });
      }

      function resetState() {
          document.getElementById('clipping-progress').classList.add('d-none');
          document.getElementById('natcap-clip-cancel-button').classList.add('d-none');
          document.getElementById('natcap-clip-done-button').classList.add('d-none');
          document.getElementById('natcap-clip-submit-button').classList.remove('d-none');
          document.getElementById('natcapClipInProgress').classList.add('d-none');
          document.getElementById('natcapClipInitOptions').classList.remove('d-none');
          document.getElementById('natcapClipDownloadClippedLayer').classList.add('d-none');
      }

      window.addEventListener('load', function() {
          new bootstrap.Modal(document.getElementById('natcapClipProgressModal')).show();
      });

      function toggleOverrideField(check_input, text_field_id) {
          var checked = document.getElementById(check_input).checked;
          var epsg_field = document.getElementById(text_field_id);

          if (epsg_field.classList.contains('d-none')) {
              epsg_field.classList.remove('d-none');
          }

          if (!checked) {
              epsg_field.classList.add('d-none');
          }
      }

      document.getElementById('natcap-clip-submit-button').addEventListener('click', submitForm);
      document.getElementById('modal-close').addEventListener('click', resetState);
      document.getElementById('natcap-clip-cancel-button').addEventListener('click', resetState);
      document.getElementById('natcap-clip-done-button').addEventListener('click', resetState);

      document.getElementById('natcapClipSettingOverrideEPSG').addEventListener(
          'click', function() {
              toggleOverrideField('natcapClipSettingOverrideEPSG', 'natcapClipSettingEPSGCode');
              toggleOverrideField(
                  'natcapClipSettingOverrideEPSG',
                  'natcapClipSettingEPSGCodeLabel');
      });

      document.getElementById('natcapClipSettingOverridePixelSize').addEventListener(
          'click', function() {
              toggleOverrideField('natcapClipSettingOverridePixelSize', 'natcapClipSettingPixelSize');
              toggleOverrideField(
                  'natcapClipSettingOverridePixelSize',
                  'natcapClipSettingPixelSizeLabel');
      });

      document.getElementById('natcapClipEnableOverrides').addEventListener(
          'click', function() {
              toggleOverrideField(
                  'natcapClipEnableOverrides',
                  'natcapClipAdvancedOptions');
              var warp = document.getElementById('natcapClipEnableOverrides').checked;
              var submit_text;
              if (warp) {
                  submit_text = 'Warp this layer!';
              } else {
                  submit_text = "Clip!"
              }
              document.getElementById('natcap-clip-submit-button').innerHTML = submit_text;
      });

      const clipping_endpoint = 'https://clipping-service-897938321824.us-west1.run.app'


      function updateSourceRasterInfo() {
          const cog = document.getElementById(clip_button_id).getAttribute('layer-url');
          console.log('updating source raster from cog ' + cog);

          var epsg_input = document.getElementById('natcapClipSettingEPSGCode');
          var cog_stats_url = `${clipping_endpoint}/info?cog_url=${encodeURIComponent(cog)}`;;
          fetch(cog_stats_url).then(response => {
            if (response.ok) {
              return response.json();
            } else {
              console.error(response);
            }
          }).then(info_json => {
            epsg_input.value = info_json['info']['stac']['proj:epsg'];
            var geotransform = info_json['info']['geoTransform'];
            // Until someone says otherwise, let's assume square pixels
            // (in SRS units)
            document.getElementById('natcapClipSettingPixelSize').value = geotransform[1];

            // Update projection information like the friendly EPSG label and the human units.
            var epsg_code = epsg_input.value;
            console.log(`Updating SRS info for ${epsg_code}`);
            fetch(`${clipping_endpoint}/epsg_info?epsg_code=${epsg_code}`, {
              method: "GET",
            }).then(epsg_response => {
              if (epsg_response.ok) {
                return epsg_response.json();
              } else {
                console.error(epsg_response);
                throw new Error(`Response status: ${epsg_response.status}`);
              }
            }).then(epsg_json => {
              if (epsg_json['status'] == 'success') {
                console.log('updating EPSG-related labels');
                document.getElementById('natcapClipSettingEPSGCodeLabel').textContent = epsg_json['epsg_name'];
                document.getElementById('natcapClipSettingPixelSizeLabel').textContent = `Units: ${epsg_json['srs_units']}`;
              } else {
                console.error("Something went wrong getting EPSG info");
                console.error(epsg_json);
              }
            });
          });
      }

      document.getElementById('natcapClipEnableOverrides').addEventListener(
          'click', function() {
              console.log('clicked enable overrides');
              updateSourceRasterInfo();
      });
    },  // end of initialize();
  };
});
