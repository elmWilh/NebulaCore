// nebula_gui_flask/static/js/layout.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

function getNavPreferenceUserId() {
    return String((window.NebulaRuntime && window.NebulaRuntime.userId) || 'anonymous');
}

function getPanelDraft() {
    try {
        const key = `nebula-panel-settings-draft:${getNavPreferenceUserId()}`;
        const raw = localStorage.getItem(key);
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_) {
        return {};
    }
}

function shouldRememberOpenSections() {
    const draft = getPanelDraft();
    if (!Object.prototype.hasOwnProperty.call(draft, 'personal.remember_open_sections')) {
        return true;
    }
    return !!draft['personal.remember_open_sections'];
}

function navStorageKey(sectionId) {
    return `nebula-nav:${getNavPreferenceUserId()}:${String(sectionId || '')}`;
}

function setSectionExpanded(title, links, expanded) {
    if (!title || !links) return;
    title.classList.toggle('collapsed', !expanded);
    links.style.maxHeight = expanded ? `${links.scrollHeight}px` : '0';
}

function toggleSection(el) {
    const section = el.parentElement;
    const sectionId = section.getAttribute('data-id');
    const links = el.nextElementSibling;
    const expanded = el.classList.contains('collapsed');

    setSectionExpanded(el, links, expanded);

    if (!shouldRememberOpenSections()) {
        localStorage.removeItem(navStorageKey(sectionId));
        return;
    }

    localStorage.setItem(navStorageKey(sectionId), expanded ? 'expanded' : 'collapsed');
}

function toggleUserMenu(e) {
    e.stopPropagation();
    document.getElementById('userMenu')?.classList.toggle('show');
}

function syncHeaderScrollState() {
    const header = document.querySelector('.header');
    const content = document.querySelector('.content');
    if (!header || !content) return;
    header.classList.toggle('is-scrolled', content.scrollTop > 12);
}

document.addEventListener('click', (event) => {
    if (!event.target.closest('.user-dropdown')) {
        document.getElementById('userMenu')?.classList.remove('show');
    }
});

document.addEventListener('DOMContentLoaded', () => {
    const rememberSections = shouldRememberOpenSections();

    document.querySelectorAll('.nav-section').forEach((section) => {
        const sectionId = section.getAttribute('data-id');
        const title = section.querySelector('.nav-section-title');
        const links = section.querySelector('.nav-links');
        if (!title || !links) return;

        const hasActiveChild = links.querySelector('.active') !== null;
        const savedState = rememberSections ? localStorage.getItem(navStorageKey(sectionId)) : null;
        const expanded = hasActiveChild || savedState === 'expanded';

        if (!rememberSections) {
            localStorage.removeItem(navStorageKey(sectionId));
        }

        setSectionExpanded(title, links, expanded);
    });

    const content = document.querySelector('.content');
    if (content) {
        content.addEventListener('scroll', syncHeaderScrollState, { passive: true });
        syncHeaderScrollState();
    }
});

window.addEventListener('nebula:theme-applied', syncHeaderScrollState);
