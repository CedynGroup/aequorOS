// Next's font test hook consumes this URL-to-CSS map. With no remote @font-face
// declarations, Playwright uses Next's generated fallback fonts without any
// Google CSS or font-file requests. Keep all layout/login families and weights
// explicit so a new font request fails as a missing fixture, without the network.
module.exports = {
  "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap":
    "/* Playwright: use the generated Inter fallback. */",
  "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap":
    "/* Playwright: use the generated IBM Plex Mono fallback. */",
  "https://fonts.googleapis.com/css2?family=Fraunces:wght@500&display=swap":
    "/* Playwright: use the generated Fraunces fallback. */",
};
