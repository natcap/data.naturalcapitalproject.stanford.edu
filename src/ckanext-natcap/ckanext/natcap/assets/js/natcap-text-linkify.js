ckan.module("natcap-text-linkify", function($, _) {
  "use strict";

  return {
    options: {
      debug: false,
    },

    // Adapted from https://stackoverflow.com/a/49634926
    initialize: function () {
      const textDiv = $(this.el);
      console.log(textDiv);
      var replaceText = textDiv.innerText;

      //URLs starting with http://, https://, or ftp://
      var replacePattern = /(\b(https?|ftp):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/gim;
      replaceText.replace(replacePattern '<a href="$1" target="_blank">$1</a>');

      //URLs starting with "www." (without // before it, or it'd re-link the ones done above).
      replacePattern2 = /(^|[^\/])(www\.[\S]+(\b|$))/gim;
      replacedText = replacedText.replace(replacePattern2, '$1<a href="http://$2" target="_blank">$2</a>');

      //Change email addresses to mailto:: links.
      replacePattern3 = /(([a-zA-Z0-9\-\_\.])+@[a-zA-Z\_]+?(\.[a-zA-Z]{2,6})+)/gim;
      replacedText = replacedText.replace(replacePattern3, '<a href="mailto:$1">$1</a>');

      $(this.el).html(replaceText);
    }
  }
});
