// nebula_gui_flask/static/js/pages/index.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

  function toggleMenu(el) {
    el.classList.toggle('open');
    const submenu = el.nextElementSibling;
    if (el.classList.contains('open')) {
      submenu.style.maxHeight = submenu.scrollHeight + "px";
    } else {
      submenu.style.maxHeight = "0px";
    }
  }

  // On Load — expand active sections
  document.querySelectorAll('.menu-item.active').forEach(item => {
    if (item.classList.contains('open') || item.querySelector('.submenu a.active')) {
      // Small delay to ensure scrollHeight is calculated correctly
      setTimeout(() => {
        const submenu = item.nextElementSibling;
        submenu.style.maxHeight = submenu.scrollHeight + "px";
      }, 50);
    }
  });

