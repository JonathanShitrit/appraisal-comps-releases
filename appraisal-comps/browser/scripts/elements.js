/* elements.js - lists what can be clicked or typed into on the page.
   Returns {origin, url, items}. Each item has n, kind, name (every label the
   element has, joined with " | "), value, selector, and its box x, y, w, h in
   viewport pixels. Hidden and off-screen elements are left out; at most 200.
   It only reads; it changes nothing on the page. browser_js runs it as one
   expression, so send this file's text unchanged. */
(function listElements() {
  const maxItems = 200;
  const maxNameLength = 120;
  const maxValueLength = 80;
  const candidateSelector = 'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="menuitem"], [role="tab"], [role="checkbox"], [role="option"]';
  const buttonInputTypes = ["button", "submit", "reset", "image"];

  function collapse(text) {
    return String(text).replace(/\s+/g, " ").trim();
  }

  function isButtonInput(element) {
    return element.tagName === "INPUT" && buttonInputTypes.indexOf(element.type) !== -1;
  }

  function kindOf(element) {
    const tag = element.tagName;
    if (tag === "A") {
      return "link";
    }
    if (tag === "BUTTON" || isButtonInput(element)) {
      return "button";
    }
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
      return tag.toLowerCase();
    }
    if (element.getAttribute("role") === "link") {
      return "link";
    }
    return "button";
  }

  function nameOf(element) {
    const labelText = element.labels && element.labels.length > 0 ? element.labels[0].innerText : "";
    const ownText = isButtonInput(element) ? element.value : element.innerText;
    const candidates = [
      ownText,
      element.getAttribute("aria-label"),
      labelText,
      element.getAttribute("title"),
      element.getAttribute("placeholder")
    ];
    const names = [];
    for (const candidate of candidates) {
      if (!candidate) {
        continue;
      }
      const cleaned = collapse(candidate);
      if (cleaned !== "" && names.indexOf(cleaned) === -1) {
        names.push(cleaned);
      }
    }
    return names.join(" | ").slice(0, maxNameLength);
  }

  function valueOf(element, kind) {
    if (kind !== "input" && kind !== "select" && kind !== "textarea") {
      return "";
    }
    if (element.type === "password") {
      return "";
    }
    return String(element.value).slice(0, maxValueLength);
  }

  function pathStep(element) {
    let position = 1;
    for (const sibling of element.parentElement.children) {
      if (sibling === element) {
        break;
      }
      if (sibling.tagName === element.tagName) {
        position += 1;
      }
    }
    return element.tagName.toLowerCase() + ":nth-of-type(" + position + ")";
  }

  function selectorOf(element) {
    if (element.id) {
      const idSelector = "#" + CSS.escape(element.id);
      if (document.querySelectorAll(idSelector).length === 1) {
        return idSelector;
      }
    }
    const steps = [];
    let current = element;
    while (current && current !== document.body) {
      steps.unshift(pathStep(current));
      current = current.parentElement;
    }
    steps.unshift("body");
    return steps.join(" > ");
  }

  function isShown(element, box) {
    if (element.tagName === "INPUT" && element.type === "hidden") {
      return false;
    }
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") {
      return false;
    }
    if (box.width <= 0 || box.height <= 0) {
      return false;
    }
    const overlapsHorizontally = box.right > 0 && box.left < window.innerWidth;
    const overlapsVertically = box.bottom > 0 && box.top < window.innerHeight;
    return overlapsHorizontally && overlapsVertically;
  }

  const items = [];
  for (const element of document.querySelectorAll(candidateSelector)) {
    if (items.length >= maxItems) {
      break;
    }
    const box = element.getBoundingClientRect();
    if (!isShown(element, box)) {
      continue;
    }
    const kind = kindOf(element);
    items.push({
      n: items.length + 1,
      kind: kind,
      name: nameOf(element),
      value: valueOf(element, kind),
      selector: selectorOf(element),
      x: Math.round(box.left),
      y: Math.round(box.top),
      w: Math.round(box.width),
      h: Math.round(box.height)
    });
  }
  return {origin: location.origin, url: location.href, items: items};
})()
