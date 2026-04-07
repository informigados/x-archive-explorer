document.addEventListener("DOMContentLoaded", () => {
  const languageSelect = document.querySelector("[data-language-select]");
  const navToggle = document.querySelector("[data-nav-toggle]");
  const navMenu = document.querySelector("[data-nav-menu]");
  const navBackdrop = document.querySelector("[data-nav-backdrop]");

  const setNavOpen = (open) => {
    if (!navToggle || !navMenu) {
      return;
    }
    navMenu.classList.toggle("is-open", open);
    navToggle.classList.toggle("is-open", open);
    navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    const label = open ? navToggle.getAttribute("data-label-close") : navToggle.getAttribute("data-label-open");
    if (label) {
      navToggle.setAttribute("aria-label", label);
    }
    if (navBackdrop) {
      navBackdrop.classList.toggle("is-open", open);
    }
  };

  if (languageSelect && languageSelect.form) {
    languageSelect.addEventListener("change", () => {
      setNavOpen(false);
      languageSelect.form.requestSubmit();
    });
  }

  if (navToggle && navMenu) {
    navToggle.addEventListener("click", () => {
      const nextState = !navMenu.classList.contains("is-open");
      setNavOpen(nextState);
    });

    navMenu.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => setNavOpen(false));
    });

    if (navBackdrop) {
      navBackdrop.addEventListener("click", () => setNavOpen(false));
    }

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        setNavOpen(false);
      }
    });

    document.addEventListener("click", (event) => {
      if (!navMenu.classList.contains("is-open")) {
        return;
      }
      const target = event.target;
      if (target instanceof Node && !navMenu.contains(target) && !navToggle.contains(target)) {
        setNavOpen(false);
      }
    });

    window.addEventListener("resize", () => {
      if (window.innerWidth > 900) {
        setNavOpen(false);
      }
    });
  }

  const forms = document.querySelectorAll("form");
  forms.forEach((form) => {
    const confirmMessage = form.getAttribute("data-confirm");
    if (confirmMessage) {
      form.addEventListener("submit", (event) => {
        if (!window.confirm(confirmMessage)) {
          event.preventDefault();
          event.stopPropagation();
        }
      });
    }

    if (form.classList.contains("lang-form")) {
      return;
    }

    form.addEventListener("submit", () => {
      const submitButtons = form.querySelectorAll('button[type="submit"], input[type="submit"]');
      submitButtons.forEach((button) => {
        if (button.disabled) {
          return;
        }
        const loadingText = button.getAttribute("data-submit-loading");
        if (loadingText && button.tagName.toLowerCase() === "button") {
          button.dataset.originalText = button.textContent || "";
          button.textContent = loadingText;
        }
        button.disabled = true;
      });
    });
  });
});
