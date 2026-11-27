/**
 * Script do Painel Web de Revisão Antes vs Depois
 * - Contador em tempo real de caracteres do título (limite de 60 caracteres no Mercado Livre)
 * - Confirmação de ações críticas (Rejeitar, Rollback, Publicar)
 * - Toggle de visualização do painel Dry-Run JSON
 */
document.addEventListener("DOMContentLoaded", function () {
  const titleInput = document.getElementById("id-proposed-title");
  const titleCounter = document.getElementById("id-title-counter");
  const maxTitleLength = 60;

  function updateTitleCounter() {
    if (!titleInput || !titleCounter) {
      return;
    }
    const len = titleInput.value.length;
    titleCounter.textContent = len + "/" + maxTitleLength + " caracteres";
    if (len > maxTitleLength) {
      titleCounter.classList.add("diff-field__counter--over");
      titleInput.classList.add("form-control--warning");
    } else {
      titleCounter.classList.remove("diff-field__counter--over");
      titleInput.classList.remove("form-control--warning");
    }
  }

  if (titleInput) {
    titleInput.addEventListener("input", updateTitleCounter);
    updateTitleCounter();
  }

  // Confirmação em botões com atributo data-confirm
  const confirmButtons = document.querySelectorAll("[data-confirm]");
  confirmButtons.forEach(function (button) {
    button.addEventListener("click", function (event) {
      const message = button.getAttribute("data-confirm");
      if (message && !window.confirm(message)) {
        event.preventDefault();
      }
    });
  });

  // Toggle opcional do bloco de JSON Dry-Run
  const toggleDryRunBtn = document.getElementById("id-toggle-dryrun");
  const dryRunPanel = document.getElementById("id-dryrun-panel");
  if (toggleDryRunBtn && dryRunPanel) {
    toggleDryRunBtn.addEventListener("click", function () {
      const isHidden = dryRunPanel.style.display === "none";
      dryRunPanel.style.display = isHidden ? "block" : "none";
    });
  }
});
