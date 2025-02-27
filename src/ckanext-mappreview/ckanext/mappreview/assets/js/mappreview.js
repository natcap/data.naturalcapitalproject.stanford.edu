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
          map.addSource('bbox', {type: 'geojson', data: bbox_geojson});
          map.addSource('vertices', {type: 'geojson', data: _vertices()});
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

      function natcapClipLayer(layer_name) {
        console.log(`Calling natcapClipLayer with ${layer_name}`);
        var layer_details = undefined;
        for (var layer in config.layers) {
          if (layer.name == layer_name) {
            layer_details = layer;
            break;
          }
        }
        console.log(layer_details);
        console.log('finished natcapClipLayer');
      }

      class ClippingControl{
        onAdd(map) {

            // config.layers attributes relevant to us
            // name - filename (e.g. awc.tif)
            // type - "raster"
            // url - the clipping url

            this._map = map;
            this._container = document.createElement('div');
            this._container.className = 'mapboxgl-ctrl';
            this._container.textContent = 'Hello, world';
            this._container = $(this._container);

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
                <button type="button" class="btn btn-secondary">
                  Clip this layer
                </button>`;
              this._container.get('button').bind('click', function(event) {
                console.log('single-raster button click handler');
                natcapClipLayer(rasters[0].name);
              });
            } else {
              var raster_string = "";
              for (const raster_layer of rasters) {
                raster_string += `
                  <li>
                    <a class="dropdown-item
                       href="#"
                       layer_name='${raster_layer.name}'>
                      ${raster_layer.name}
                    </a>
                  </li>\n`
              }
              this._container.innerHTML = `
                <div class="dropup">
                  <button class='btn btn-secondary dropdown-toggle'
                          data-bs-toggle='dropdown'
                          aria-expanded='false'
                          id='natCapClipLayer'>Clip this layer</button>
                  <ul class="dropdown-menu">
                    ${raster_string}
                  </ul>
                </div>
              `;
              this._container.get('a.dropdown-item').bind('click', function(event) {
                console.log(event);
                console.log('We should call natcapClipLayer here, but where is the data?');
              });
            }
            //this._container.querySelector('#natCapClipLayer').addEventListener('click', this._toggleClippingOptions);
            //
            //this._clipping_options = document.createElement('div');
            //this._clipping_options.id = 'clipOptions';
            //this._clipping_options.className = 'collapse card card-body';
            //this._clipping_options.innerHTML = `
            //  <h3>Clipping Options</h3>
            //  <div class="form-group row">
            //    <label for="targetEPSG" class="col-sm-4 col-form-label">Target coordinate system EPSG</label>
            //    <div class="col-sm-4">
            //      <input type="text"
            //             class="form-control"
            //             id="targetEPSG"
            //             placeholder="4326">
            //    </div>
            //    <div class="col-sm-4">
            //      <label for="targetEPSG"
            //             id="targetEPSGInfo"
            //             class="col-form-label"></label>
            //    </div>
            // </div>

            // <button class="btn btn-primary float-end"
            //         id="submit"
            //         type="submit"
            //         data-bs-toggle="modal"
            //         data-bs-target="#progress-modal">Clip layer to selected bounding box</button>
            //         <!-- onclick="clip_cog();getData()" -->
            //`
            //
            //this._container.appendChild(this._clipping_options);
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

    },  // end of initialize();
  };
});
