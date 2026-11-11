const header = document.querySelector("[data-landing-header]");
const sections = [...document.querySelectorAll("[data-landing-section]")];
const labels = [...document.querySelectorAll("[data-location]")];
let active;
let frame;

function updateLocation() {
  frame = undefined;
  const visibleTop = header.getBoundingClientRect().bottom;
  const readingLine = visibleTop + (window.innerHeight - visibleTop) / 3;
  let section = sections[0];
  if (window.scrollY > 0) {
    for (const candidate of sections) {
      if (candidate.getBoundingClientRect().top <= readingLine) section = candidate;
    }
  }
  const atEnd = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2;
  const anchor = sections.find((candidate) => candidate.id && "#" + candidate.id === location.hash);
  if (atEnd && window.scrollY > 0) section = sections.at(-1);
  if (anchor) {
    const anchorTop = anchor.getBoundingClientRect().top;
    const margin = Number.parseFloat(getComputedStyle(anchor).scrollMarginTop);
    // An anchor can stop at the end of the page before it reaches its usual offset.
    if (Math.abs(anchorTop - margin) <= 2 || (atEnd && anchorTop > margin)) section = anchor;
  }

  const current = section.dataset.landingSection;
  if (current === active) return;
  active = current;
  for (const label of labels) {
    if (label.dataset.location === current) label.setAttribute("aria-current", "location");
    else label.removeAttribute("aria-current");
  }
}

function requestLocationUpdate() {
  if (frame === undefined) frame = requestAnimationFrame(updateLocation);
}

if (header && sections.length) {
  updateLocation();
  window.addEventListener("scroll", requestLocationUpdate, { passive: true });
  window.addEventListener("resize", requestLocationUpdate);
  window.addEventListener("hashchange", requestLocationUpdate);
  window.addEventListener("pageshow", requestLocationUpdate);
}
