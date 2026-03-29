// nebula_gui_flask/static/js/i18n.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

(function () {
    const runtime = window.NebulaRuntime || {};
    const userId = String(runtime.userId || 'anonymous');
    const SETTINGS_STORAGE_KEY = `nebula-panel-settings-draft:${userId}`;
    const LOCALE_STORAGE_KEY = `nebula-panel-locale:${userId}`;
    const FALLBACK_LOCALE = 'en';

    let localeCatalog = [];
    let fallbackMessages = {};
    let fallbackAliases = {};
    let activeMessages = {};
    let currentLocale = FALLBACK_LOCALE;
    let mutationObserver = null;

    function loadDraft() {
        try {
            const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
            if (!raw) return {};
            const parsed = JSON.parse(raw);
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch (_) {
            return {};
        }
    }

    function savePreferredLocale(locale) {
        try {
            localStorage.setItem(LOCALE_STORAGE_KEY, String(locale || FALLBACK_LOCALE));
        } catch (_) {}
    }

    function getPreferredLocaleFromSettings() {
        const draft = loadDraft();
        const value = String(draft['language.panel_language'] || '').trim().toLowerCase();
        return value;
    }

    function normalizeLocale(code) {
        return String(code || '').trim().toLowerCase().replace('_', '-');
    }

    function resolveCatalogLocale(code) {
        const normalized = normalizeLocale(code);
        if (!normalized) return '';
        const direct = localeCatalog.find((item) => normalizeLocale(item.code) === normalized);
        if (direct) return direct.code;
        const primary = normalized.split('-')[0];
        const primaryMatch = localeCatalog.find((item) => normalizeLocale(item.code) === primary);
        return primaryMatch ? primaryMatch.code : '';
    }

    async function fetchCatalog() {
        try {
            const response = await fetch('/api/i18n/catalog', { cache: 'no-store' });
            const payload = await response.json();
            localeCatalog = Array.isArray(payload.locales) ? payload.locales : [];
            return payload;
        } catch (_) {
            localeCatalog = [];
            return { default_locale: FALLBACK_LOCALE, locales: [] };
        }
    }

    async function fetchLocaleMessages(locale) {
        const normalized = resolveCatalogLocale(locale) || normalizeLocale(locale) || FALLBACK_LOCALE;
        const entry = localeCatalog.find((item) => normalizeLocale(item.code) === normalized);
        const localePath = entry?.path || `/static/locales/${normalized}.json`;
        try {
            const response = await fetch(localePath, { cache: 'no-store' });
            const payload = await response.json();
            return {
                messages: payload?.messages && typeof payload.messages === 'object' ? payload.messages : {},
                aliases: payload?.aliases && typeof payload.aliases === 'object' ? payload.aliases : {},
            };
        } catch (_) {
            return { messages: {}, aliases: {} };
        }
    }

    function translateString(value) {
        if (typeof value !== 'string') return value;
        const direct = activeMessages[value];
        if (typeof direct === 'string') return direct;
        const aliasKey = fallbackAliases[value];
        if (aliasKey && typeof activeMessages[aliasKey] === 'string') {
            return activeMessages[aliasKey];
        }
        return value;
    }

    function interpolate(template, params = {}) {
        return String(template).replace(/\{([a-zA-Z0-9_]+)\}/g, (_, key) => {
            return Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : `{${key}}`;
        });
    }

    function t(key, params = {}, fallback = '') {
        const raw = translateString(key);
        if (raw !== key) {
            return interpolate(raw, params);
        }
        const fallbackValue = fallback ? translateString(fallback) : key;
        return interpolate(fallbackValue, params);
    }

    function translateTextNode(node) {
        if (!node || node.nodeType !== Node.TEXT_NODE) return;
        const parentTag = node.parentElement?.tagName;
        if (parentTag && ['SCRIPT', 'STYLE', 'TEXTAREA'].includes(parentTag)) return;

        const original = node.nodeValue;
        if (!original || !/[A-Za-z]/.test(original)) return;

        const trimmed = original.trim();
        if (!trimmed) return;

        const translated = translateString(trimmed);
        if (translated === trimmed) return;

        const leading = original.match(/^\s*/)?.[0] || '';
        const trailing = original.match(/\s*$/)?.[0] || '';
        node.nodeValue = `${leading}${translated}${trailing}`;
    }

    function translateAttributes(element) {
        if (!element || element.nodeType !== Node.ELEMENT_NODE) return;
        ['placeholder', 'title', 'aria-label'].forEach((attr) => {
            const value = element.getAttribute(attr);
            if (!value || !/[A-Za-z]/.test(value)) return;
            const translated = translateString(value);
            if (translated !== value) {
                element.setAttribute(attr, translated);
            }
        });
    }

    function translateFragment(root = document.body) {
        if (!root) return;

        if (root.nodeType === Node.TEXT_NODE) {
            translateTextNode(root);
            return;
        }

        if (root.nodeType === Node.ELEMENT_NODE) {
            translateAttributes(root);
        }

        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
        let current = walker.currentNode;
        while (current) {
            if (current.nodeType === Node.TEXT_NODE) {
                translateTextNode(current);
            } else {
                translateAttributes(current);
            }
            current = walker.nextNode();
        }
    }

    function translateDocumentTitle() {
        if (!document.title) return;
        let nextTitle = document.title;
        Object.keys(fallbackAliases).sort((a, b) => b.length - a.length).forEach((source) => {
            const translated = translateString(source);
            if (translated !== source && nextTitle.includes(source)) {
                nextTitle = nextTitle.replaceAll(source, translated);
            }
        });
        document.title = nextTitle;
    }

    function startObserver() {
        if (mutationObserver || !document.body) return;
        mutationObserver = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.type === 'characterData') {
                    translateTextNode(mutation.target);
                    return;
                }
                mutation.addedNodes.forEach((node) => {
                    translateFragment(node);
                });
                if (mutation.type === 'attributes' && mutation.target instanceof Element) {
                    translateAttributes(mutation.target);
                }
            });
        });

        mutationObserver.observe(document.body, {
            subtree: true,
            childList: true,
            characterData: true,
            attributes: true,
            attributeFilter: ['placeholder', 'title', 'aria-label'],
        });
    }

    async function setLocale(locale, options = {}) {
        const { persist = true, rerender = true } = options;
        const targetLocale = resolveCatalogLocale(locale) || FALLBACK_LOCALE;
        const selectedBundle = targetLocale === FALLBACK_LOCALE
            ? { messages: fallbackMessages, aliases: fallbackAliases }
            : await fetchLocaleMessages(targetLocale);

        currentLocale = targetLocale;
        activeMessages = {
            ...fallbackMessages,
            ...(selectedBundle.messages || {}),
        };
        document.documentElement.lang = currentLocale;

        if (persist) {
            savePreferredLocale(currentLocale);
        }

        if (rerender) {
            translateDocumentTitle();
            translateFragment(document.body);
        }

        window.dispatchEvent(new CustomEvent('nebula:locale-changed', {
            detail: { locale: currentLocale },
        }));

        return currentLocale;
    }

    async function init() {
        const catalog = await fetchCatalog();
        const defaultLocale = resolveCatalogLocale(catalog.default_locale) || FALLBACK_LOCALE;
        const fallbackBundle = await fetchLocaleMessages(defaultLocale);
        fallbackMessages = fallbackBundle.messages || {};
        fallbackAliases = fallbackBundle.aliases || {};
        activeMessages = { ...fallbackMessages };

        const storedLocale = normalizeLocale(localStorage.getItem(LOCALE_STORAGE_KEY));
        const settingsLocale = getPreferredLocaleFromSettings();
        const browserLocale = normalizeLocale(navigator.language || navigator.languages?.[0] || '');
        const targetLocale =
            resolveCatalogLocale(settingsLocale) ||
            resolveCatalogLocale(storedLocale) ||
            resolveCatalogLocale(browserLocale) ||
            defaultLocale;

        await setLocale(targetLocale, { persist: false, rerender: false });

        const applyTranslations = () => {
            translateDocumentTitle();
            translateFragment(document.body);
            startObserver();
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', applyTranslations, { once: true });
        } else {
            applyTranslations();
        }
    }

    const ready = init();

    window.NebulaI18n = {
        ready,
        t,
        translateFragment,
        setLocale,
        getLocale: () => currentLocale,
        getCatalog: () => localeCatalog.slice(),
    };
})();
