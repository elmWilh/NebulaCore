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
const recoveryForm = document.getElementById('recoveryForm');
const toggleRecovery = document.getElementById('toggle-recovery');
const recoveryUsername = document.getElementById('recovery-username');
const recoveryCode = document.getElementById('recovery-code');
const recoveryNewPassword = document.getElementById('recovery-new-password');
const requestCodeBtn = document.getElementById('requestCodeBtn');
const applyResetBtn = document.getElementById('applyResetBtn');
const requestCodeText = document.getElementById('request-code-text');
const applyResetText = document.getElementById('apply-reset-text');
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
        } else if (result.detail === 'PASSWORD_RESET_REQUIRED') {
            alertMsg.innerText = 'Password setup required. Open Recovery Mode to receive a reset code.';
            if (recoveryForm) recoveryForm.style.display = 'block';
            if (recoveryUsername && !recoveryUsername.value) {
                recoveryUsername.value = (form.querySelector('input[name="username"]') || {}).value || '';
            }
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

if (toggleRecovery && recoveryForm) {
    toggleRecovery.addEventListener('click', (e) => {
        e.preventDefault();
        const open = recoveryForm.style.display !== 'none';
        recoveryForm.style.display = open ? 'none' : 'block';
    });
}

if (requestCodeBtn) {
    requestCodeBtn.addEventListener('click', async () => {
        const username = (recoveryUsername?.value || '').trim();
        if (!username) {
            alertMsg.innerText = 'Enter username first.';
            alertBox.style.display = 'block';
            return;
        }
        requestCodeBtn.disabled = true;
        requestCodeText.innerText = 'Sending...';
        alertBox.style.display = 'none';
        try {
            const payload = new FormData();
            payload.set('username', username);
            const response = await fetch('/api/auth/password-reset/request', {
                method: 'POST',
                body: payload,
            });
            const result = await response.json();
            if (response.ok) {
                alertMsg.innerText = 'If account exists, a recovery code was sent to email. Code expires in 2 minutes.';
            } else {
                alertMsg.innerText = result.detail || 'Failed to send recovery code';
            }
            alertBox.style.display = 'block';
        } catch (err) {
            alertMsg.innerText = 'Recovery request failed.';
            alertBox.style.display = 'block';
        } finally {
            requestCodeBtn.disabled = false;
            requestCodeText.innerText = 'Send Code';
        }
    });
}

if (applyResetBtn) {
    applyResetBtn.addEventListener('click', async () => {
        const username = (recoveryUsername?.value || '').trim();
        const code = (recoveryCode?.value || '').trim();
        const newPassword = recoveryNewPassword?.value || '';
        if (!username || !code || !newPassword) {
            alertMsg.innerText = 'Provide username, code and new password.';
            alertBox.style.display = 'block';
            return;
        }
        applyResetBtn.disabled = true;
        applyResetText.innerText = 'Applying...';
        alertBox.style.display = 'none';
        try {
            const payload = new FormData();
            payload.set('username', username);
            payload.set('code', code);
            payload.set('new_password', newPassword);
            const response = await fetch('/api/auth/password-reset/confirm', {
                method: 'POST',
                body: payload,
            });
            const result = await response.json();
            if (response.ok && result.status === 'password_updated') {
                alertMsg.innerText = 'Password updated. You can log in now.';
                if (recoveryNewPassword) recoveryNewPassword.value = '';
                if (recoveryCode) recoveryCode.value = '';
                const loginUsername = form.querySelector('input[name="username"]');
                if (loginUsername && !loginUsername.value) {
                    loginUsername.value = username;
                }
            } else {
                alertMsg.innerText = result.detail || 'Failed to update password';
            }
            alertBox.style.display = 'block';
        } catch (err) {
            alertMsg.innerText = 'Password reset failed.';
            alertBox.style.display = 'block';
        } finally {
            applyResetBtn.disabled = false;
            applyResetText.innerText = 'Set Password';
        }
    });
}
