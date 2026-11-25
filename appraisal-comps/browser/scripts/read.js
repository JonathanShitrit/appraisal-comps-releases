/* read.js - the plugin's page reader. Returns {origin, url, title, text}:
   the page's origin and address, its title, and its visible text cut to the
   first 100,000 characters. It only reads; it changes nothing on the page.
   browser_js runs it as one expression, so send this file's text unchanged. */
(function readPage() {
  const maxTextLength = 100000;
  const body = document.body;
  let text = "";
  if (body) {
    text = body.innerText.slice(0, maxTextLength);
  }
  return {
    origin: location.origin,
    url: location.href,
    title: document.title,
    text: text
  };
})()
