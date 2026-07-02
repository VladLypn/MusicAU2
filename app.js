const header = document.querySelector("[data-header]");
const revealItems = document.querySelectorAll(".reveal");

const setHeaderState = () => {
  header?.classList.toggle("is-scrolled", window.scrollY > 8);
};

setHeaderState();
window.addEventListener("scroll", setHeaderState, { passive: true });

if ("IntersectionObserver" in window) {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.14 }
  );

  revealItems.forEach((item) => observer.observe(item));
} else {
  revealItems.forEach((item) => item.classList.add("is-visible"));
}

const waitlistForm = document.querySelector("[data-waitlist-form]");
const waitlistShell = document.querySelector("[data-waitlist-shell]");
const waitlistSuccess = document.querySelector("[data-waitlist-success]");
const emailInput = document.querySelector("[data-email-input]");
const emailError = document.querySelector("[data-email-error]");
const formMessage = document.querySelector("[data-form-message]");
const submitButton = document.querySelector("[data-submit-button]");
const contactMethods = document.querySelector("[data-contact-methods]");
const addContactButton = document.querySelector("[data-add-contact]");
const waitlistCount = document.querySelector("[data-waitlist-count]");

const contactOptions = [
  ["discord", "Discord username"],
  ["phone", "Phone number"],
  ["reddit", "Reddit username"],
  ["telegram", "Telegram"],
  ["instagram", "Instagram"],
  ["x", "X (Twitter)"],
  ["other", "Other contact method"],
];

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/i;

const readableMessage = (value, fallback = "Something went wrong. Please try again.") => {
  if (!value) return fallback;
  if (typeof value === "string") return value;
  if (value instanceof Error) return value.message || fallback;

  if (typeof value === "object") {
    if (typeof value.error === "string") return value.error;
    if (typeof value.message === "string") return value.message;
    if (typeof value.detail === "string") return value.detail;
  }

  return fallback;
};

const setMessage = (text = "", isError = true) => {
  if (!formMessage) return;
  formMessage.textContent = readableMessage(text, "");
  formMessage.style.color = isError ? "var(--pink)" : "var(--green)";
};

const validateEmail = () => {
  if (!emailInput || !emailError) return false;

  const field = emailInput.closest(".form-field");
  const value = emailInput.value.trim();
  let error = "";

  if (!value) {
    error = "Email is required.";
  } else if (!emailPattern.test(value)) {
    error = "Please enter a valid email address.";
  }

  field?.classList.toggle("is-invalid", Boolean(error));
  emailError.textContent = error;
  return !error;
};

const createContactRow = () => {
  const row = document.createElement("div");
  row.className = "contact-row";
  row.dataset.contactRow = "";
  row.innerHTML = `
    <label>
      <span>Method</span>
      <select name="contactType">
        ${contactOptions.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
      </select>
    </label>
    <label>
      <span>Contact</span>
      <input name="contactValue" type="text" placeholder="@musicau_player">
    </label>
  `;
  return row;
};

const readContactMethods = () => {
  return [...document.querySelectorAll("[data-contact-row]")]
    .map((row) => {
      const type = row.querySelector("[name='contactType']")?.value;
      const value = row.querySelector("[name='contactValue']")?.value.trim();
      return type && value ? { type, value } : null;
    })
    .filter(Boolean);
};

const setLoading = (isLoading) => {
  waitlistForm?.classList.toggle("is-loading", isLoading);
  if (submitButton) {
    submitButton.disabled = isLoading;
  }
};

const showConfetti = () => {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const colors = ["#a6ff62", "#35d8ff", "#ff4fb8", "#ffc24b", "#8d5cff"];
  const pieces = 34;

  for (let index = 0; index < pieces; index += 1) {
    const piece = document.createElement("span");
    piece.className = "confetti-piece";
    piece.style.left = `${Math.random() * 100}vw`;
    piece.style.background = colors[index % colors.length];
    piece.style.setProperty("--fall-x", `${Math.random() * 180 - 90}px`);
    piece.style.animationDelay = `${Math.random() * 260}ms`;
    document.body.append(piece);
    piece.addEventListener("animationend", () => piece.remove(), { once: true });
  }
};

const refreshWaitlistCount = async () => {
  if (!waitlistCount) return;

  try {
    const response = await fetch("/api/waitlist/count", {
      headers: { Accept: "application/json" },
    });

    if (!response.ok) return;

    const data = await response.json();
    const count = Number(data.count || 0);

    if (count > 0) {
      waitlistCount.hidden = false;
      waitlistCount.querySelector("strong").textContent = count.toLocaleString();
      waitlistCount.querySelector("span").textContent =
        count === 1 ? "player already on the waitlist" : "players already on the waitlist";
    }
  } catch {
    waitlistCount.hidden = true;
  }
};

emailInput?.addEventListener("input", () => {
  validateEmail();
  setMessage("");
});

addContactButton?.addEventListener("click", () => {
  const rows = document.querySelectorAll("[data-contact-row]");
  if (rows.length >= 4) {
    setMessage("You can add up to four contact methods.", true);
    return;
  }

  contactMethods?.append(createContactRow());
  setMessage("");
});

waitlistForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("");

  if (!validateEmail()) {
    emailInput?.focus();
    return;
  }

  const formData = new FormData(waitlistForm);
  const payload = {
    email: String(formData.get("email") || "").trim(),
    contacts: readContactMethods(),
    referralSource: String(formData.get("referralSource") || "").trim(),
    featureRequest: String(formData.get("featureRequest") || "").trim(),
    website: String(formData.get("website") || "").trim(),
  };

  setLoading(true);

  try {
    const response = await fetch("/api/waitlist", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(readableMessage(data, "Something went wrong. Please try again."));
    }

    waitlistForm.hidden = true;
    if (waitlistSuccess) {
      waitlistSuccess.hidden = false;
      waitlistSuccess.focus?.();
    }
    waitlistShell?.classList.add("is-success");
    showConfetti();
    refreshWaitlistCount();
  } catch (error) {
    setMessage(readableMessage(error), true);
  } finally {
    setLoading(false);
  }
});

refreshWaitlistCount();
