// nebula_gui_flask/static/js/pages/userlogin.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

document.getElementById('loginForm').onsubmit = async (e) => {
    e.preventDefault();
    const btn = document.getElementById('submitBtn');
    const alertBox = document.getElementById('auth-alert');
    const alertMsg = document.getElementById('alert-message');
    const btnText = document.getElementById('btn-text');
    const otpGroup = document.getElementById('otp-group');
    const otpInput = document.getElementById('otp-input');
    
    btn.disabled = true;
    btnText.innerText = "Verifying...";
    alertBox.style.display = 'none';

    try {
        const response = await fetch('/login', {
            method: 'POST',
            body: new FormData(e.target)
        });

        const result = await response.json();

        if (response.ok) {
            btnText.innerText = "Redirecting...";
            window.location.href = result.redirect;
        } else {
            if (result.detail === '2FA_REQUIRED') {
                alertMsg.innerText = "2FA required: enter code from Google Authenticator";
                otpGroup.style.display = 'block';
                otpInput.required = true;
                otpInput.focus();
            } else if (result.detail === 'INVALID_2FA_CODE') {
                alertMsg.innerText = "Invalid authenticator code";
                otpGroup.style.display = 'block';
                otpInput.required = true;
                otpInput.focus();
            } else {
                if (otpGroup.style.display !== 'none') {
                    otpInput.value = '';
                }
                alertMsg.innerText = result.detail || "Access Denied";
            }
            alertBox.style.display = 'block';
            btn.disabled = false;
            btnText.innerText = "Authorize Session";
        }
    } catch (err) {
        alertMsg.innerText = "Nebula Core Connection Error";
        alertBox.style.display = 'block';
        btn.disabled = false;
        btnText.innerText = "Authorize Session";
    }
};

