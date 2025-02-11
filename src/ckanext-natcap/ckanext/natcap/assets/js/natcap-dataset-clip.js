this.ckan.module('natcap-dataset-clip', function($, _) {
  return {
    options: {
      dataset: "",
    }

    _onClick: function () {
      // ideally: show the clipper in a modal
      // For now, just open a new tab with the correct URLs.

    }

    initialize: function () {
      this.clipButton = this.$('.icon-button-clip');
      this.clipButton.on('click', $.proxy(this._onClick, this));
    }
  };
});

