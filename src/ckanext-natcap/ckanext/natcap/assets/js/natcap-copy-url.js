ckan.module("natcap-copy-url", function ($, _) {
  "use strict";
  return {
    options: {
      debug: false,
      url: '',
    },

    initialize: function () {
      const copyDiv = $(this.el);  // We expect this to be attached to the div itself.

      copyDiv.click(() => {
        navigator.clipboard.writeText(this.options.url).then(() => {
          console.log('Copied! ' + this.options.url);

          const originalBG = copyDiv.css('background-image');
          copyDiv.append('<i title="Copied!" class="fa-solid fa-check"i style="color: #4BAB39"></i>');
          copyDiv.css('background-image', 'none');

          setTimeout(function () {
            copyDiv.css('background-image', originalBG);
            copyDiv.find('i').remove();
          }, 5000);
        });
      });
    }
  }
});
