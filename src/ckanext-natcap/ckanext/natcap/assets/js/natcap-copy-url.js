ckan.module("natcap-copy-url", function ($, _) {
  "use strict";
  return {
    options: {
      debug: false,
      url: '',
    },

    initialize: function () {
      const copyDiv = $(this.el).find('.resource-copy-url');

      copyDiv.click(() => {
        navigator.clipboard.writeText(this.options.url).then(() => {
          console.log('Copied! ' + this.options.url);
        });
      });
    }
  }
});
