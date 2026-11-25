'use strict'

// 「开机时自动启动」 after the app was installed again into another folder: what Windows starts at login is still the
// old place. Only an entry that points at an app no longer there is taken over, and only one left switched on.

const path = require('node:path')

/** `items`: app.getLoginItemSettings().launchItems. Whether one of them is this app's from where it used to be. */
function movedAway(items, name, execPath, exists) {
  return (items ?? []).some(
    (item) =>
      item.name === name &&
      item.enabled !== false &&
      path.basename(item.path).toLowerCase() === path.basename(execPath).toLowerCase() &&
      path.resolve(item.path).toLowerCase() !== path.resolve(execPath).toLowerCase() &&
      !exists(item.path),
  )
}

module.exports = { movedAway }
