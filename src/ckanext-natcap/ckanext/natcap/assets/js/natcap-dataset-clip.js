this.ckan.module('natcap-dataset-clip', function($, _) {
  'use strict';
  return {
    options: {
      dataset: "",
    },

    _onClick: function () {
      // For now, just open a new tab with the correct URLs.
      var endpoint = 'https://clipping-service-897938321824.us-west1.run.app';
      var url = `${endpoint}/clip?cog_url=${this.options.dataset}`;

      var map = document.getElementById('map').parentElement;

      var lat = map.getAttribute('center-lat');
      if (lat !== undefined && lat !== null) {
        url += `&lat=${lat}`;
      }

      var lng = map.getAttribute('center-lng');
      if (lng !== undefined && lng !== null) {
        url += `&lng=${lng}`;
      }

      var zoom = map.getAttribute('zoom');
      if (zoom !== undefined && zoom !== null) {
        url += `&zoom=${zoom}`;
      }
      console.log(`Opening new tab for clipping ${url}`);
      window.open(url, '_blank').focus();
    },

    initialize: function () {
      this.clipButton = $(this.el);  // We expect this to be attached to the div itself.
      this.clipButton.click(() => {
        this._onClick();
      });
    },
  };
});

