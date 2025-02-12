this.ckan.module('natcap-dataset-clip', function($, _) {
  'use strict';
  return {
    options: {
      dataset: "",
    },

    _onClick: function () {
      console.log('Clicked the clip button');
      // For now, just open a new tab with the correct URLs.
      var endpoint = 'https://clipping-service-897938321824.us-west1.run.app';
      var url = `${endpoint}/clip?cog_url=${this.options.dataset}`;

      var map = this.$('div.mappreview')[0];

      var lat = map.attr('center-lat');
      if (lat !== undefined) {
        url += `&lat=${lat}`;
      }

      var lng = map.attr('center-lng');
      if (lng !== undefined) {
        url += `&lng=${lng}`;
      }

      var zoom = map.attr('zoom');
      if (zoom !== undefined) {
        url += `&zoom=${zoom}`;
      }
      console.log(`Attempting to open ${url}`);
      window.open(url, '_blank').focus();
    },

    initialize: function () {
      this.clipButton = $(this.el);  // We expect this to be attached to the div itself.
      console.log('Initializing natcap-dataset-clip ' + this.clipButton);
      this.clipButton.click(() => {
        this._onClick();
      });
    },
  };
});

