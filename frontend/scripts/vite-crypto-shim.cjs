// Vite 5 reads getRandomValues from the Node crypto default export.
// Some Node builds expose it only through crypto.webcrypto.
const crypto = require('node:crypto')

if (typeof crypto.getRandomValues !== 'function' && crypto.webcrypto) {
  crypto.getRandomValues = crypto.webcrypto.getRandomValues.bind(crypto.webcrypto)
}
