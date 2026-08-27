document.addEventListener("DOMContentLoaded", () => {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.querySelector(".sidebar-backdrop");
  const toggles = document.querySelectorAll("[data-toggle-sidebar]");

  const setSidebar = (open) => {
    if (!sidebar || !backdrop) return;
    sidebar.classList.toggle("is-open", open);
    backdrop.classList.toggle("is-open", open);
    document.body.style.overflow = open ? "hidden" : "";
  };

  toggles.forEach((btn) => {
    btn.addEventListener("click", () => setSidebar(!sidebar.classList.contains("is-open")));
  });

  document.querySelectorAll("form[data-loading]").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("[type='submit']");
      if (btn) {
        btn.disabled = true;
        btn.dataset.originalText = btn.innerHTML;
        btn.innerHTML = '<i class="ti ti-loader-2 ti-spin"></i> Traitement...';
      }
    });
  });

  const closeModal = (modal) => {
    if (!modal) return;
    modal.classList.remove("show");
    modal.style.display = "";
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  };

  document.querySelectorAll("[data-bs-dismiss='modal']").forEach((btn) => {
    btn.addEventListener("click", () => closeModal(btn.closest(".modal")));
  });

  document.querySelectorAll(".modal").forEach((modal) => {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) closeModal(modal);
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeModal(document.querySelector(".modal.show"));
  });
});
