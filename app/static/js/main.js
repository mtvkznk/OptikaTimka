const header = document.querySelector(".site-header");
const menuButton = document.querySelector(".menu-button");
const tabLinks = document.querySelectorAll("[data-tab-link]");
const tabPanels = document.querySelectorAll("[data-tab-panel]");
const knownTabs = new Set([...tabPanels].map((panel) => panel.dataset.tabPanel));

const form = document.querySelector("[data-appointment-form]");
const statusNode = document.querySelector(".form-status");
const steps = document.querySelectorAll("[data-form-step]");
const stepDots = document.querySelectorAll("[data-step-dot]");
const previousButton = document.querySelector("[data-prev-step]");
const nextButton = document.querySelector("[data-next-step]");
const submitButton = document.querySelector("[data-submit-step]");
const ticketPreview = document.querySelector("[data-ticket-preview]");
let currentStep = 1;

const dateInput = form?.elements.visit_date;
if (dateInput) {
  dateInput.min = new Date().toISOString().slice(0, 10);
}

function closeMenu() {
  header?.classList.remove("menu-open");
  menuButton?.setAttribute("aria-expanded", "false");
}

function setActiveTab(tabName, updateHash = true) {
  const activeTab = knownTabs.has(tabName) ? tabName : "home";

  tabPanels.forEach((panel) => {
    const isActive = panel.dataset.tabPanel === activeTab;
    panel.hidden = !isActive;
    panel.classList.toggle("is-active", isActive);
  });

  tabLinks.forEach((link) => {
    const isActive = link.dataset.tabLink === activeTab;
    if (link.getAttribute("role") === "tab") {
      link.setAttribute("aria-selected", String(isActive));
    }
  });

  closeMenu();

  if (updateHash && window.location.hash !== `#${activeTab}`) {
    history.pushState(null, "", `#${activeTab}`);
  }
}

menuButton?.addEventListener("click", () => {
  const isOpen = header.classList.toggle("menu-open");
  menuButton.setAttribute("aria-expanded", String(isOpen));
});

tabLinks.forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    setActiveTab(link.dataset.tabLink);
  });
});

window.addEventListener("hashchange", () => {
  setActiveTab(window.location.hash.replace("#", ""), false);
});

setActiveTab(window.location.hash.replace("#", ""), false);

function fieldsForStep(stepNumber) {
  return [...document.querySelectorAll(`[data-form-step="${stepNumber}"] input, [data-form-step="${stepNumber}"] select, [data-form-step="${stepNumber}"] textarea`)];
}

function validateStep(stepNumber) {
  const fields = fieldsForStep(stepNumber);
  const invalidField = fields.find((field) => !field.checkValidity());

  if (invalidField) {
    invalidField.reportValidity();
    return false;
  }

  return true;
}

function getSelectedText(selectName) {
  const select = form?.elements[selectName];
  return select?.options[select.selectedIndex]?.textContent.trim() || "";
}

function formatVisitDate(dateValue) {
  if (!dateValue) {
    return "";
  }

  return new Intl.DateTimeFormat("uk-UA", {
    day: "2-digit",
    month: "long",
    year: "numeric",
  }).format(new Date(`${dateValue}T12:00:00`));
}

function updateTicketPreview() {
  if (!form || !ticketPreview) {
    return;
  }

  const formData = new FormData(form);
  const name = formData.get("name") || "Пацієнт";
  const service = getSelectedText("service_id") || "Послуга не обрана";
  const branch = formData.get("branch") || "Філія не обрана";
  const date = formatVisitDate(formData.get("visit_date"));
  const time = formData.get("visit_time") || "";

  ticketPreview.replaceChildren();

  const label = document.createElement("span");
  label.textContent = "Талон е-запису";

  const title = document.createElement("strong");
  title.textContent = name;

  const details = document.createElement("p");
  details.innerHTML = "";
  [service, branch, `${date}${time ? `, ${time}` : ""}`].forEach((line, index) => {
    if (index > 0) {
      details.append(document.createElement("br"));
    }
    details.append(document.createTextNode(line));
  });

  ticketPreview.append(label, title, details);
}

function setStep(stepNumber) {
  currentStep = Math.min(Math.max(stepNumber, 1), steps.length);

  steps.forEach((step) => {
    const isActive = Number(step.dataset.formStep) === currentStep;
    step.hidden = !isActive;
    step.classList.toggle("is-active", isActive);
  });

  stepDots.forEach((dot) => {
    const dotStep = Number(dot.dataset.stepDot);
    dot.classList.toggle("is-current", dotStep === currentStep);
    dot.classList.toggle("is-complete", dotStep < currentStep);
  });

  previousButton.disabled = currentStep === 1;
  nextButton.hidden = currentStep === steps.length;
  submitButton.hidden = currentStep !== steps.length;

  if (currentStep === steps.length) {
    updateTicketPreview();
  }
}

nextButton?.addEventListener("click", () => {
  if (validateStep(currentStep)) {
    setStep(currentStep + 1);
  }
});

previousButton?.addEventListener("click", () => {
  setStep(currentStep - 1);
});

form?.addEventListener("input", () => {
  if (currentStep === steps.length) {
    updateTicketPreview();
  }
});

form?.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!validateStep(currentStep)) {
    return;
  }

  statusNode.className = "form-status";
  statusNode.textContent = "Формуємо талон е-запису...";
  submitButton.disabled = true;

  const formData = new FormData(form);
  const extraDetails = [
    formData.get("email") ? `Email: ${formData.get("email")}` : null,
    formData.get("branch") ? `Філія: ${formData.get("branch")}` : null,
    formData.get("visit_date") ? `Дата: ${formatVisitDate(formData.get("visit_date"))}` : null,
    formData.get("visit_time") ? `Час: ${formData.get("visit_time")}` : null,
    formData.get("message") ? `Коментар: ${formData.get("message")}` : null,
  ].filter(Boolean);

  const payload = {
    name: formData.get("name"),
    phone: formData.get("phone"),
    service_id: formData.get("service_id") ? Number(formData.get("service_id")) : null,
    message: extraDetails.join("\n") || null,
  };

  try {
    const response = await fetch("/api/appointments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error("Request failed");
    }

    form.reset();
    setStep(1);
    statusNode.classList.add("is-success");
    statusNode.textContent = "Готово! Талон е-запису сформовано, адміністратор зв'яжеться з вами.";
  } catch {
    statusNode.classList.add("is-error");
    statusNode.textContent = "Не вдалося створити запис. Перевірте дані та спробуйте ще раз.";
  } finally {
    submitButton.disabled = false;
  }
});

setStep(1);
