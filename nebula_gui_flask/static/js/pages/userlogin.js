// nebula_gui_flask/static/js/pages/userlogin.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

const LOCK_UNTIL_STORAGE_KEY = 'nebula_login_lock_until';
const form = document.getElementById('loginForm');
const btn = document.getElementById('submitBtn');
const alertBox = document.getElementById('auth-alert');
const alertMsg = document.getElementById('alert-message');
const btnText = document.getElementById('btn-text');
const otpGroup = document.getElementById('otp-group');
const otpInput = document.getElementById('otp-input');
let lockTimer = null;

function setLockUntil(untilMs) {
    localStorage.setItem(LOCK_UNTIL_STORAGE_KEY, String(untilMs));
}

function clearLockUntil() {
    localStorage.removeItem(LOCK_UNTIL_STORAGE_KEY);
}

function getLockSecondsLeft() {
    const raw = localStorage.getItem(LOCK_UNTIL_STORAGE_KEY);
    const until = Number(raw || 0);
    if (!until || Number.isNaN(until)) {
        return 0;
    }
    return Math.max(0, Math.ceil((until - Date.now()) / 1000));
}

function setDefaultButtonState() {
    btn.disabled = false;
    btnText.innerText = 'Authorize Session';
}

function renderLockState() {
    const secondsLeft = getLockSecondsLeft();
    if (secondsLeft <= 0) {
        clearLockUntil();
        setDefaultButtonState();
        return false;
    }
    btn.disabled = true;
    btnText.innerText = `Try again in ${secondsLeft}s`;
    alertMsg.innerText = `Too many login attempts. Wait ${secondsLeft}s before next try.`;
    alertBox.style.display = 'block';
    return true;
}

function startLockCountdown(lockSeconds) {
    setLockUntil(Date.now() + (lockSeconds * 1000));
    if (lockTimer) {
        clearInterval(lockTimer);
    }
    renderLockState();
    lockTimer = setInterval(() => {
        const stillLocked = renderLockState();
        if (!stillLocked && lockTimer) {
            clearInterval(lockTimer);
            lockTimer = null;
        }
    }, 1000);
}

function parseRetryAfterSeconds(response, result) {
    const headerVal = Number(response.headers.get('Retry-After') || 0);
    if (headerVal > 0) {
        return headerVal;
    }
    const detail = result && typeof result.detail === 'string' ? result.detail : '';
    const match = detail.match(/(\d+)\s*s/);
    if (!match) {
        return 0;
    }
    return Number(match[1]) || 0;
}

if (renderLockState()) {
    lockTimer = setInterval(() => {
        const stillLocked = renderLockState();
        if (!stillLocked && lockTimer) {
            clearInterval(lockTimer);
            lockTimer = null;
        }
    }, 1000);
}

form.onsubmit = async (e) => {
    e.preventDefault();
    if (renderLockState()) {
        return;
    }

    btn.disabled = true;
    btnText.innerText = 'Verifying...';
    alertBox.style.display = 'none';

    try {
        const response = await fetch('/login', {
            method: 'POST',
            body: new FormData(e.target)
        });

        const result = await response.json();

        if (response.ok) {
            clearLockUntil();
            btnText.innerText = 'Redirecting...';
            window.location.href = result.redirect;
            return;
        }

        if (response.status === 429) {
            const lockSeconds = parseRetryAfterSeconds(response, result);
            if (lockSeconds > 0) {
                startLockCountdown(lockSeconds);
                return;
            }
        }

        if (result.detail === '2FA_REQUIRED') {
            alertMsg.innerText = '2FA required: enter code from Google Authenticator';
            otpGroup.style.display = 'block';
            otpInput.required = true;
            otpInput.focus();
        } else if (result.detail === 'INVALID_2FA_CODE') {
            alertMsg.innerText = 'Invalid authenticator code';
            otpGroup.style.display = 'block';
            otpInput.required = true;
            otpInput.focus();
        } else {
            if (otpGroup.style.display !== 'none') {
                otpInput.value = '';
            }
            alertMsg.innerText = result.detail || 'Access Denied';
        }
        alertBox.style.display = 'block';
        setDefaultButtonState();
    } catch (err) {
        alertMsg.innerText = 'Nebula Core Connection Error';
        alertBox.style.display = 'block';
        setDefaultButtonState();
    }
};
