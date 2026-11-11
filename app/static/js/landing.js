const header = document.querySelector("[data-landing-header]");
const sections = [...document.querySelectorAll("[data-landing-section]")];
const labels = [...document.querySelectorAll("[data-location]")];
let active;
let frame;

function updateLocation() {
  frame = undefined;
  const readingLine = header.getBoundingClientRect().bottom + 64;
  let section = sections[0];
  for (const candidate of sections) {
    if (candidate.getBoundingClientRect().top <= readingLine) section = candidate;
  }
  const atEnd = window.scrollY > 0
    && window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2;
  if (atEnd) section = sections.at(-1);

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
  window.addEventListener("pageshow", requestLocationUpdate);
}
