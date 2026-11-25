'use strict'

// Whose chat the chat window was asked to show. A page still loading (the app just started, nest came back)
// hears nothing sent to it, so it asks when it is ready (preload.js); what it has shown is not shown again.

class OpenBot {
  constructor() {
    this.id = ''
  }

  /** The window is being opened for this one ('' for nobody in particular). */
  ask(id) {
    this.id = id
  }

  /** The page has it. One asked for since then stays. */
  shown(id) {
    if (this.id === id) this.id = ''
  }
}

module.exports = { OpenBot }
