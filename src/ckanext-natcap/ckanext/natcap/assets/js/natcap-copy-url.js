ckan.module("natcap-copy-url", function ($, _) {
  "use strict";
  return {
    options: {
      debug: false,
      url: '',
    },

    initialize: function () {
      const copyDiv = $(this.el);  // We expect this to be attached to the div itself.
      console.log('Copy URL module initialized ' + copyDiv);

      copyDiv.click(() => {
        navigator.clipboard.writeText(this.options.url).then(() => {
          console.log('Copied! ' + this.options.url);
        });
      });
    }
  }
});
