// nebula_gui_flask/static/js/pages/settings.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

const settingsContext = window.NebulaSettings || {};
const settingsUserId = String(settingsContext.userId || 'anonymous');
const SETTINGS_STORAGE_KEY = `nebula-panel-settings-draft:${settingsUserId}`;
const SETTINGS_THEME_STORAGE_KEY = `nebula-panel-theme:${settingsUserId}`;
let settingsThemeCatalog = [];
let settingsLocaleCatalog = [];

function t(key, params = {}, fallback = '') {
    return window.NebulaI18n?.t(key, params, fallback) || fallback || key;
}

function applyPanelThemeSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return;
    const vars = snapshot.vars && typeof snapshot.vars === 'object' ? snapshot.vars : {};
    Object.entries(vars).forEach(([key, value]) => {
        document.documentElement.style.setProperty(key, String(value));
    });
    const themeId = String(snapshot.id || 'custom');
    document.documentElement.dataset.panelTheme = themeId;
    if (document.body) {
        document.body.dataset.panelTheme = themeId;
    }
    const themeColor = String(snapshot.theme_color || '').trim();
    const meta = document.querySelector('meta[name="theme-color"]');
    if (themeColor && meta) meta.setAttribute('content', themeColor);
    window.dispatchEvent(new CustomEvent('nebula:theme-applied', {
        detail: { snapshot },
    }));
}

async function loadThemeCatalog() {
    const select = document.getElementById('personal-theme-select');
    const description = document.getElementById('personal-theme-description');
    if (!select) return;
    try {
        const res = await fetch('/static/panel_themes/index.json', { cache: 'no-store' });
        const data = await res.json();
        settingsThemeCatalog = Array.isArray(data) ? data : [];
        select.innerHTML = '';
        settingsThemeCatalog.forEach((theme) => {
            const option = document.createElement('option');
            option.value = String(theme.id || '');
            option.textContent = String(theme.title || theme.id || 'Theme');
            select.appendChild(option);
        });
        if (!settingsThemeCatalog.length) {
            const option = document.createElement('option');
            option.value = 'nebula';
            option.textContent = 'Nebula';
            select.appendChild(option);
            if (description) description.textContent = t('settings.theme_catalog_failed');
        }
    } catch (_) {
        select.innerHTML = '<option value="nebula">Nebula</option>';
        if (description) description.textContent = t('settings.theme_catalog_unavailable');
    }
}

function updateThemeDescription(themeId) {
    const description = document.getElementById('personal-theme-description');
    if (!description) return;
    const theme = settingsThemeCatalog.find((item) => String(item.id) === String(themeId));
    description.textContent = theme?.description || t('settings.default_theme_description');
}

async function applyThemeById(themeId, persist = true) {
    const entry = settingsThemeCatalog.find((item) => String(item.id) === String(themeId));
    if (!entry || !entry.file) {
        updateThemeDescription(themeId);
        return;
    }
    try {
        const res = await fetch(`/static/panel_themes/${encodeURIComponent(entry.file)}`, { cache: 'no-store' });
        const payload = await res.json();
        applyPanelThemeSnapshot(payload);
        updateThemeDescription(themeId);
        if (persist) {
            localStorage.setItem(SETTINGS_THEME_STORAGE_KEY, JSON.stringify(payload));
        }
    } catch (_) {
        updateThemeDescription(themeId);
        showSettingsToast(t('settings.theme_apply_failed'), 'warn');
    }
}

async function loadLocaleCatalog() {
    const select = document.getElementById('language-panel-select');
    if (!select) return;
    try {
        const response = await fetch('/api/i18n/catalog', { cache: 'no-store' });
        const payload = await response.json();
        settingsLocaleCatalog = Array.isArray(payload.locales) ? payload.locales : [];
        select.innerHTML = '';
        settingsLocaleCatalog.forEach((locale) => {
            const option = document.createElement('option');
            option.value = String(locale.code || '');
            option.textContent = String(locale.native_name || locale.name || locale.code || '').trim() || 'Unknown';
            select.appendChild(option);
        });
        if (!settingsLocaleCatalog.length) {
            select.innerHTML = '<option value="en">English</option>';
        }
    } catch (_) {
        select.innerHTML = '<option value="en">English</option>';
    }
}

function applyPersonalPreferencesPreview() {
    const draft = collectSettingsDraft();
    document.body.classList.toggle('reduce-motion', !!draft['personal.reduce_motion']);
    document.body.classList.toggle('compact-tables', !!draft['personal.compact_tables']);
    document.body.classList.remove('content-width-default', 'content-width-narrow', 'content-width-wide');
    document.body.classList.add(`content-width-${String(draft['personal.content_width'] || 'default')}`);
}

function getSavedThemeId() {
    try {
        const raw = localStorage.getItem(SETTINGS_THEME_STORAGE_KEY);
        if (!raw) return '';
        const parsed = JSON.parse(raw);
        return String(parsed?.id || '').trim();
    } catch (_) {
        return '';
    }
}

function getSettingInputs() {
    return Array.from(document.querySelectorAll('[data-setting-key]'));
}

function showSettingsToast(message, level = 'ok', lifeMs = 2600) {
    const zone = document.getElementById('settings-toast-zone');
    if (!zone) return;
    const node = document.createElement('div');
    node.className = `settings-toast ${level}`;
    node.textContent = message;
    zone.appendChild(node);
    setTimeout(() => node.remove(), lifeMs);
}

function updateDraftStatus(text) {
    const node = document.getElementById('settings-draft-status');
    if (node) node.textContent = text;
}

function collectSettingsDraft() {
    const payload = {};
    getSettingInputs().forEach((input) => {
        if (input.disabled) return;
        const key = String(input.dataset.settingKey || '').trim();
        if (!key) return;
        payload[key] = input.type === 'checkbox' ? !!input.checked : String(input.value ?? '');
    });
    return payload;
}

function defaultValueFor(input) {
    const raw = input.dataset.default;
    if (input.type === 'checkbox') {
        return String(raw || '').toLowerCase() === 'true';
    }
    return raw ?? '';
}

function applyDraft(payload = {}) {
    getSettingInputs().forEach((input) => {
        const key = String(input.dataset.settingKey || '').trim();
        if (!key) return;
        const fallback = defaultValueFor(input);
        const value = Object.prototype.hasOwnProperty.call(payload, key) ? payload[key] : fallback;
        if (input.type === 'checkbox') {
            input.checked = !!value;
        } else {
            input.value = value;
        }
    });
}

function loadSettingsDraft() {
    try {
        const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
        if (!raw) {
            applyDraft({});
            const savedThemeId = getSavedThemeId();
            if (savedThemeId) {
                const themeSelect = document.getElementById('personal-theme-select');
                if (themeSelect) themeSelect.value = savedThemeId;
            }
            applyPersonalPreferencesPreview();
            updateDraftStatus(t('settings.page_status'));
            return;
        }
        const parsed = JSON.parse(raw);
        applyDraft(parsed && typeof parsed === 'object' ? parsed : {});
        applyPersonalPreferencesPreview();
        updateDraftStatus(t('settings.loaded_preferences', { user: settingsUserId }));
    } catch (_) {
        applyDraft({});
        applyPersonalPreferencesPreview();
        updateDraftStatus(t('settings.defaults_loaded'));
    }
}

function saveSettingsDraft() {
    const payload = {
        ...collectSettingsDraft(),
        _saved_at: new Date().toISOString(),
        _user_id: settingsUserId
    };
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(payload));
    applyPersonalPreferencesPreview();
    updateDraftStatus(t('settings.saved_status', {
        user: settingsUserId,
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }));
    showSettingsToast(t('settings.saved'), 'ok');
}

function resetSettingsDraft() {
    localStorage.removeItem(SETTINGS_STORAGE_KEY);
    applyDraft({});
    applyPersonalPreferencesPreview();
    const fallbackTheme = settingsThemeCatalog[0]?.id || 'nebula';
    const themeSelect = document.getElementById('personal-theme-select');
    if (themeSelect) themeSelect.value = fallbackTheme;
    applyThemeById(fallbackTheme, true);
    updateDraftStatus(t('settings.reset_status'));
    showSettingsToast(t('settings.reset'), 'warn');
}

function exportSettingsDraft() {
    const payload = collectSettingsDraft();
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `nebula-settings-draft-${settingsUserId}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
    showSettingsToast(t('settings.exported'), 'ok');
}

function renderProfileCapabilities() {
    const zone = document.getElementById('settings-profile-capabilities');
    if (!zone) return;

    const capabilities = [
        t('settings.workspace_theme_and_layout'),
        t('settings.default_page_and_sidebar_memory'),
        t('settings.per_user_panel_behavior')
    ];

    if (settingsContext.isStaff) {
        capabilities.push(t('settings.admin_modules_and_policy'));
        capabilities.push(t('settings.security_and_operations_categories'));
    } else {
        capabilities.push(t('settings.assigned_workspace_modules'));
        capabilities.push(t('settings.role_aware_profile_scope', {
            role: String(settingsContext.roleTag || 'user').toUpperCase(),
        }));
    }

    zone.innerHTML = capabilities
        .map((label) => `<div class="check-item"><i class="bi bi-check2-circle"></i> ${label}</div>`)
        .join('');
}

function disableUnavailableSections() {
    document.querySelectorAll('.settings-section[data-availability="planned"]').forEach((section) => {
        section.querySelectorAll('input, select, textarea, button').forEach((input) => {
            input.disabled = true;
        });
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    await window.NebulaI18n?.ready;
    await loadThemeCatalog();
    await loadLocaleCatalog();
    renderProfileCapabilities();
    loadSettingsDraft();
    disableUnavailableSections();
    const themeSelect = document.getElementById('personal-theme-select');
    if (themeSelect) {
        updateThemeDescription(themeSelect.value);
        await applyThemeById(themeSelect.value, false);
    }
    getSettingInputs().forEach((input) => {
        input.addEventListener('change', () => {
            updateDraftStatus(t('settings.unsaved_changes'));
            if (input.dataset.settingKey === 'personal.theme_id') {
                applyThemeById(input.value, true);
            }
            if (input.dataset.settingKey === 'language.panel_language') {
                window.NebulaI18n?.setLocale(input.value);
            }
            if (input.dataset.settingKey === 'personal.reduce_motion' || input.dataset.settingKey === 'personal.compact_tables' || input.dataset.settingKey === 'personal.content_width') {
                applyPersonalPreferencesPreview();
            }
        });
        if (input.tagName === 'INPUT' && input.type !== 'checkbox') {
            input.addEventListener('input', () => {
                updateDraftStatus(t('settings.unsaved_changes'));
            });
        }
        if (input.tagName === 'SELECT' && input.dataset.settingKey === 'personal.content_width') {
            input.addEventListener('input', applyPersonalPreferencesPreview);
        }
    });
});

window.saveSettingsDraft = saveSettingsDraft;
window.resetSettingsDraft = resetSettingsDraft;
window.exportSettingsDraft = exportSettingsDraft;
