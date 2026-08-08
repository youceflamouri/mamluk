function showToast(message) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toast.classList.remove("show"), 2200);
}

document.addEventListener("DOMContentLoaded", () => {
  const toastMessage = document.body.getAttribute("data-toast");
  if (toastMessage) showToast(toastMessage);

  // Side menu drawer
  const menuToggle = document.getElementById("menuToggle");
  const sidePanel = document.getElementById("sidePanel");
  const backdrop = document.getElementById("drawerBackdrop");

  function closeMenu() {
    if (!sidePanel || !backdrop) return;
    sidePanel.classList.remove("open");
    backdrop.hidden = true;
  }

  if (menuToggle && sidePanel && backdrop) {
    menuToggle.addEventListener("click", () => {
      sidePanel.classList.toggle("open");
      backdrop.hidden = !sidePanel.classList.contains("open");
    });
    backdrop.addEventListener("click", closeMenu);
    sidePanel.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeMenu));
  }

  // Size / quantity picker — used on the product detail page's add-to-cart form
  document.querySelectorAll(".size-qty-widget").forEach((widget) => {
    const sizeHidden = widget.querySelector(".size-hidden");

    widget.querySelectorAll(".size-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        widget.querySelectorAll(".size-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        if (sizeHidden) sizeHidden.value = btn.dataset.size;
      });
    });

    widget.querySelectorAll(".qty-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const input = widget.querySelector(".qty-input");
        const current = Number(input.value || 1);
        const next = btn.dataset.action === "plus" ? current + 1 : Math.max(1, current - 1);
        input.value = Math.min(10, Math.max(1, next));
      });
    });
  });

  // Inputs/selects marked .auto-submit submit their form on change
  // (keeps CSP script-src strict — no inline onchange="" attributes).
  document.querySelectorAll(".auto-submit").forEach((el) => {
    el.addEventListener("change", () => el.form && el.form.submit());
  });

  // Forms marked .confirm-submit show a confirmation dialog before submitting
  // (keeps CSP script-src strict — no inline onsubmit="" attributes).
  document.querySelectorAll(".confirm-submit").forEach((form) => {
    form.addEventListener("submit", (e) => {
      const message = form.dataset.confirm || "Are you sure?";
      if (!window.confirm(message)) e.preventDefault();
    });
  });

  // Apply chart bar/fill sizes from data-pct (kept out of inline style="" so
  // the CSP can stay strict with no 'unsafe-inline' for style-src).
  document.querySelectorAll(".bar[data-pct]").forEach((el) => {
    el.style.height = `${el.dataset.pct}%`;
  });
  document.querySelectorAll(".hbar-fill[data-pct]").forEach((el) => {
    el.style.width = `${el.dataset.pct}%`;
  });

  // Register the service worker for PWA/offline support
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        console.info("Service worker registration skipped in this environment.");
      });
    });
  }
});
