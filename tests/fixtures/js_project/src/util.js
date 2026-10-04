const helper = (msg) => `[app] ${msg}`;

function log(msg) {
  console.log(helper(msg));
}

exports.warn = function (msg) {
  log(msg);
};

module.exports = { log };
