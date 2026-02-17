    function toggleSection(el) {
      const section = el.parentElement;
      const sectionId = section.getAttribute('data-id');
      const links = el.nextElementSibling;
      
      el.classList.toggle('collapsed');
      
      if (el.classList.contains('collapsed')) {
        links.style.maxHeight = '0';
        localStorage.setItem('nav-' + sectionId, 'collapsed');
      } else {
        links.style.maxHeight = links.scrollHeight + 'px';
        localStorage.setItem('nav-' + sectionId, 'expanded');
      }
    }

    function toggleUserMenu(e) {
        e.stopPropagation();
        document.getElementById('userMenu').classList.toggle('show');
    }

    document.addEventListener('click', (event) => {
      if (!event.target.closest('.user-dropdown')) {
        document.getElementById('userMenu')?.classList.remove('show');
      }
    });

    document.addEventListener('DOMContentLoaded', () => {
      document.querySelectorAll('.nav-section').forEach(section => {
        const sectionId = section.getAttribute('data-id');
        const title = section.querySelector('.nav-section-title');
        const links = section.querySelector('.nav-links');
        
        const hasActiveChild = links.querySelector('.active') !== null;
        const savedState = localStorage.getItem('nav-' + sectionId);

        if (hasActiveChild || savedState === 'expanded' || (savedState === null && hasActiveChild)) {
          title.classList.remove('collapsed');
          links.style.maxHeight = links.scrollHeight + 'px';
        } else {
          title.classList.add('collapsed');
          links.style.maxHeight = '0';
        }
      });
    });

