/* ============================================================================
   AutoBill Buddy - Shared Application Logic
   Used by all pages
============================================================================ */

// ---- STATE ----
let _session = null;
let _supabase = null;
let _allInventoryItems = [];
let _allItemPrices = {};
let _appReady = false;

// ---- XSS ESCAPE ----
function escapeHtml(str) {
    if (str == null) return '';
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(String(str)));
    return div.innerHTML;
}

// shorthand
const esc = (s) => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

// shorthand also exported to window for billing page
window.esc = esc;
window.debounce = debounce;

// ---- DEBOUNCE ----
function debounce(fn, delay = 300) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
    };
}

// ---- API CALL WRAPPER ----
async function api(endpoint, method = 'GET', body = null) {
    if (!_session) {
        console.warn('No session for API call:', endpoint);
        return { error: true, status: 401, detail: 'Not authenticated' };
    }

    try {
        const headers = {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${_session.access_token}`
        };
        const opts = { method, headers };
        if (body) opts.body = JSON.stringify(body);

        const res = await fetch(endpoint, opts);
        if (!res.ok) {
            const text = await res.text();
            if (res.status === 401) {
                showToast('Session expired. Please refresh.', 'warning');
            }
            return { error: true, status: res.status, detail: text };
        }
        try {
            return await res.json();
        } catch (parseErr) {
            console.error('JSON parse error:', parseErr);
            return { error: true, status: res.status, detail: 'Invalid JSON response' };
        }
    } catch (e) {
        console.error('Fetch error:', e);
        showToast('Network error. Please try again.', 'error');
        return { error: true, status: 0, detail: 'Network error' };
    }
}

// ---- TOAST NOTIFICATIONS ----
function showToast(message, type = 'info') {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const icons = {
        success: 'fa-check-circle',
        error: 'fa-times-circle',
        warning: 'fa-exclamation-circle',
        info: 'fa-info-circle'
    };

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<i class="fas ${icons[type] || icons.info}"></i><span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'toast-out 0.3s ease forwards';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ---- THEME TOGGLE ----
function initTheme() {
    const theme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', theme);

    const btn = document.getElementById('theme-toggle-btn');
    if (btn) {
        btn.innerHTML = `<i class="fas ${theme === 'dark' ? 'fa-sun' : 'fa-moon'}"></i>`;
        btn.onclick = () => {
            const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            localStorage.setItem('theme', next);
            btn.innerHTML = `<i class="fas ${next === 'dark' ? 'fa-sun' : 'fa-moon'}"></i>`;
        };
    }
}

// ---- MOBILE SIDEBAR ----
function initSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    const toggle = document.getElementById('sidebar-mobile-toggle');

    // Highlight active page
    const currentPage = window.location.pathname.split('/').pop() || 'dashboard.html';
    document.querySelectorAll('.sidebar-item[data-page]').forEach(item => {
        const pg = item.getAttribute('data-page');
        if (pg === currentPage || (currentPage === '' && pg === 'dashboard.html')) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });

    // Mobile toggle
    if (toggle && sidebar) {
        toggle.onclick = () => {
            sidebar.classList.toggle('mobile-open');
            if (overlay) overlay.classList.toggle('show');
        };
    }

    if (overlay) {
        overlay.onclick = () => {
            sidebar?.classList.remove('mobile-open');
            overlay.classList.remove('show');
        };
    }
}

// ---- AUTH ----
async function initApp() {
    // Load Supabase from config
    if (!_supabase) {
        try {
            const cfgRes = await fetch('/config');
            const cfg = await cfgRes.json();

            const { createClient } = await import('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm');
            _supabase = createClient(
                window.location.origin + '/supabase-proxy',
                cfg.anon_key
            );
        } catch (e) {
            console.error('Supabase init failed:', e);
        }
    }

    const GUEST_MAGIC = 'GUEST_MODE_NO_AUTH';
    let token = localStorage.getItem('token');
    let refreshToken = null;

    // Load session with separate tokens
    try {
        const storedSession = localStorage.getItem('session');
        if (storedSession) {
            const parsed = JSON.parse(storedSession);
            token = parsed.access_token;
            refreshToken = parsed.refresh_token;
        }
    } catch (e) {
        console.error('Failed to parse stored session:', e);
    }

    // Get guest token if none
    if (!token) {
        try {
            const res = await fetch('/get-guest-token', { method: 'POST' });
            if (res.ok) {
                const data = await res.json();
                token = data.access_token;
                localStorage.setItem('token', token);
                localStorage.setItem('user_email', 'guest@autobill.com');
            }
        } catch (err) {
            console.error('Guest login failed', err);
        }
    }

    // Set up session
    if (token) {
        if (token === GUEST_MAGIC) {
            _session = {
                access_token: GUEST_MAGIC,
                user: {
                    email: 'guest@autobill.com',
                    id: 'guest',
                    user_metadata: { shop_name: 'Demo Store' }
                }
            };
            onLoginSuccess(_session);
        } else if (_supabase) {
            try {
                // Use stored refresh token if available
                if (refreshToken) {
                    const { data } = await _supabase.auth.setSession({
                        access_token: token,
                        refresh_token: refreshToken
                    });
                    if (data?.session) {
                        _session = data.session;
                        // Store updated tokens
                        localStorage.setItem('session', JSON.stringify({
                            access_token: data.session.access_token,
                            refresh_token: data.session.refresh_token
                        }));
                        onLoginSuccess(_session);
                    }
                } else if (token) {
                    // No refresh token - try to refresh from server
                    const { data, error } = await _supabase.auth.refreshSession();
                    if (error) {
                        console.error('Session refresh error:', error);
                        localStorage.removeItem('token');
                        localStorage.removeItem('session');
                    } else if (data?.session) {
                        _session = data.session;
                        localStorage.setItem('session', JSON.stringify({
                            access_token: data.session.access_token,
                            refresh_token: data.session.refresh_token
                        }));
                        onLoginSuccess(_session);
                    }
                }
            } catch (e) {
                console.error('Session setup error:', e);
                localStorage.removeItem('token');
                localStorage.removeItem('session');
            }
        }
    }

    // Logout handler
    const logoutBtn = document.getElementById('sidebar-logout-btn');
    if (logoutBtn) {
        logoutBtn.onclick = async () => {
            localStorage.clear();
            _session = null;
            if (_supabase) await _supabase.auth.signOut();
            window.location.reload();
        };
    }

    _appReady = true;
}

function onLoginSuccess(s) {
    _session = s;
    const meta = s.user?.user_metadata || {};
    const isGuest = !s.user?.email || s.user?.email === 'guest@autobill.com' || s.access_token === 'GUEST_MODE_NO_AUTH';
    const shopName = meta.shop_name || s.user?.email?.split('@')[0] || 'Guest';
    const email = s.user?.email || 'guest@autobill.com';

    // Toggle sidebar footer sections
    const authSection = document.getElementById('sidebar-authenticated');
    const loginSection = document.getElementById('sidebar-login-section');

    if (isGuest) {
        // Show login button
        if (authSection) authSection.style.display = 'none';
        if (loginSection) loginSection.style.display = 'block';
    } else {
        // Show user info
        if (authSection) authSection.style.display = 'block';
        if (loginSection) loginSection.style.display = 'none';

        // Update user name
        const nameEl = document.getElementById('sidebar-user-name');
        if (nameEl) nameEl.textContent = shopName;

        // Update avatar initial
        const avatarEl = document.getElementById('sidebar-avatar');
        if (avatarEl) avatarEl.textContent = shopName.charAt(0).toUpperCase();

        // Update user plan
        const planEl = document.getElementById('sidebar-user-plan');
        if (planEl) planEl.textContent = email;
    }

    // Call page-specific onLogin if defined
    if (typeof onLogin === 'function') {
        onLogin(s);
    }
}

// ---- LOGIN MODAL ----
window.openLoginModal = function() {
    const modal = document.getElementById('login-modal');
    if (!modal) return;
    showLogin();
    closeLoginMessage();
    modal.classList.remove('hidden');
    // Focus email
    setTimeout(() => document.getElementById('login-email')?.focus(), 100);
};

window.closeLoginModal = function() {
    const modal = document.getElementById('login-modal');
    if (modal) modal.classList.add('hidden');
    closeLoginMessage();
};

window.showSignup = function() {
    const loginForm = document.getElementById('login-form-section');
    const signupForm = document.getElementById('signup-form-section');
    const title = document.getElementById('login-modal-title');
    if (loginForm) loginForm.style.display = 'none';
    if (signupForm) signupForm.style.display = 'block';
    if (title) title.innerHTML = '<i class="fas fa-user-plus" style="color:var(--sidebar-accent)"></i> Create Account';
    closeLoginMessage();
    setTimeout(() => document.getElementById('signup-email')?.focus(), 100);
};

window.showLogin = function() {
    const loginForm = document.getElementById('login-form-section');
    const signupForm = document.getElementById('signup-form-section');
    const title = document.getElementById('login-modal-title');
    if (loginForm) loginForm.style.display = 'block';
    if (signupForm) signupForm.style.display = 'none';
    if (title) title.innerHTML = '<i class="fas fa-right-to-bracket" style="color:var(--sidebar-accent)"></i> Login / Sign Up';
    closeLoginMessage();
    setTimeout(() => document.getElementById('login-email')?.focus(), 100);
};

function showLoginMessage(msg, type) {
    const el = document.getElementById('login-message');
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'block';
    el.style.background = type === 'error' ? 'var(--accent-red-light)' : 'var(--accent-green-light)';
    el.style.color = type === 'error' ? 'var(--status-error)' : 'var(--status-success)';
}

function closeLoginMessage() {
    const el = document.getElementById('login-message');
    if (el) { el.style.display = 'none'; el.textContent = ''; }
}

function setLoginLoading(loading, btnId, label) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.disabled = loading;
    btn.innerHTML = loading
        ? `<i class="fas fa-spinner fa-spin"></i> Please wait...`
        : label;
}

window.submitLogin = async function() {
    const email = document.getElementById('login-email')?.value?.trim();
    const password = document.getElementById('login-password')?.value;
    const shopName = document.getElementById('login-shop-name')?.value?.trim();

    if (!email || !password) {
        showLoginMessage('Please enter email and password', 'error');
        return;
    }

    if (!_supabase) {
        showLoginMessage('Authentication service unavailable. Please refresh the page.', 'error');
        return;
    }

    setLoginLoading(true, 'login-submit-btn', '<i class="fas fa-right-to-bracket"></i> Login');

    try {
        const { data, error } = await _supabase.auth.signInWithPassword({ email, password });

        if (error) {
            showLoginMessage(error.message || 'Login failed. Check your credentials.', 'error');
        } else if (data?.session) {
            _session = data.session;
            // Store both tokens for session restoration
            localStorage.setItem('token', data.session.access_token);
            localStorage.setItem('session', JSON.stringify({
                access_token: data.session.access_token,
                refresh_token: data.session.refresh_token
            }));
            localStorage.setItem('user_email', email);
            if (shopName) localStorage.setItem('shop_name', shopName);
            closeLoginModal();
            showToast('Welcome back! Logged in successfully.', 'success');
            onLoginSuccess(_session);
            // Reload to refresh page data
            if (typeof loadPageData === 'function') loadPageData();
        }
    } catch (e) {
        showLoginMessage('Login failed. Please try again.', 'error');
    }

    setLoginLoading(false, 'login-submit-btn', '<i class="fas fa-right-to-bracket"></i> Login');
};

window.submitSignup = async function() {
    const email = document.getElementById('signup-email')?.value?.trim();
    const password = document.getElementById('signup-password')?.value;
    const shopName = document.getElementById('signup-shop-name')?.value?.trim();

    if (!email || !password) {
        showLoginMessage('Please enter email and password', 'error');
        return;
    }
    if (password.length < 6) {
        showLoginMessage('Password must be at least 6 characters', 'error');
        return;
    }

    if (!_supabase) {
        showLoginMessage('Authentication service unavailable. Please refresh the page.', 'error');
        return;
    }

    setLoginLoading(true, 'signup-submit-btn', '<i class="fas fa-user-plus"></i> Create Account');

    try {
        const { data, error } = await _supabase.auth.signUp({
            email,
            password,
            options: {
                data: { shop_name: shopName || email.split('@')[0] }
            }
        });

        if (error) {
            showLoginMessage(error.message || 'Signup failed. Please try again.', 'error');
        } else if (data?.session) {
            // Email verification may be required - some Supabase setups auto-confirm
            _session = data.session;
            // Store both tokens for session restoration
            localStorage.setItem('token', data.session.access_token);
            localStorage.setItem('session', JSON.stringify({
                access_token: data.session.access_token,
                refresh_token: data.session.refresh_token
            }));
            localStorage.setItem('user_email', email);
            if (shopName) localStorage.setItem('shop_name', shopName);
            closeLoginModal();
            showToast('Account created! Welcome to AutoBill Buddy.', 'success');
            onLoginSuccess(_session);
            if (typeof loadPageData === 'function') loadPageData();
        } else if (data?.user && !data?.session) {
            // Email verification needed
            showLoginMessage('Account created! Check your email to confirm, then login.', 'success');
            showLogin();
        }
    } catch (e) {
        showLoginMessage('Signup failed. Please try again.', 'error');
    }

    setLoginLoading(false, 'signup-submit-btn', '<i class="fas fa-user-plus"></i> Create Account');
};

// ---- INLINE PROMPT ----
let _promptResolve = null;

function showInlinePrompt(title, placeholder = '', defaultValue = '', inputType = 'text') {
    return new Promise((resolve) => {
        _promptResolve = resolve;
        const modal = document.getElementById('inline-prompt-modal');
        if (!modal) { resolve(defaultValue); return; }

        const titleEl = document.getElementById('inline-prompt-title');
        const input = document.getElementById('inline-prompt-input');
        const submitBtn = document.getElementById('inline-prompt-submit');
        const cancelBtn = document.getElementById('inline-prompt-cancel');

        if (titleEl) titleEl.innerHTML = `<i class="fas fa-edit" style="color:var(--accent-green)"></i> ${escapeHtml(title)}`;
        if (input) {
            input.type = inputType;
            input.placeholder = placeholder;
            input.value = defaultValue;
        }

        modal.classList.remove('hidden');
        setTimeout(() => input?.focus(), 100);

        const cleanup = () => {
            modal.classList.add('hidden');
            submitBtn?.removeEventListener('click', handleSubmit);
            cancelBtn?.removeEventListener('click', handleCancel);
            input?.removeEventListener('keydown', handleKey);
        };

        const handleSubmit = () => { cleanup(); resolve(input?.value ?? ''); };
        const handleCancel = () => { cleanup(); resolve(null); };
        const handleKey = (e) => {
            if (e.key === 'Enter') handleSubmit();
            if (e.key === 'Escape') handleCancel();
        };

        submitBtn?.addEventListener('click', handleSubmit);
        cancelBtn?.addEventListener('click', handleCancel);
        input?.addEventListener('keydown', handleKey);
    });
}

function closeInlinePrompt() {
    const modal = document.getElementById('inline-prompt-modal');
    if (modal) modal.classList.add('hidden');
    if (_promptResolve) { _promptResolve(null); _promptResolve = null; }
}

// ---- FORMAT CURRENCY ----
function formatCurrency(amount) {
    return '₹' + (parseFloat(amount) || 0).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

// ---- FORMAT DATE ----
function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

// ---- LOAD PAGE DATA (called by each page) ----
async function loadDashboardData(onData) {
    if (!onData) return;
    const [stats, inventory] = await Promise.all([
        api('/sales/today'),
        api('/inventory')
    ]);
    onData(stats, inventory);
}

// ---- EXPORT TO WINDOW ----
window.app = {
    api,
    esc,
    escapeHtml,
    showToast,
    initApp,
    initTheme,
    initSidebar,
    showInlinePrompt,
    closeInlinePrompt,
    formatCurrency,
    formatDate,
    loadDashboardData,
    openLoginModal,
    closeLoginModal,
    get session() { return _session; },
    get inventory() { return _allInventoryItems; },
    set inventory(v) { _allInventoryItems = v; },
    get prices() { return _allItemPrices; },
    set prices(v) { _allItemPrices = v; }
};

// Add Enter key support to login forms
document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        const modal = document.getElementById('login-modal');
        if (!modal || modal.classList.contains('hidden')) return;
        const loginFormVisible = document.getElementById('login-form-section')?.style.display !== 'none';
        if (loginFormVisible) {
            submitLogin();
        } else {
            submitSignup();
        }
    }
    if (e.key === 'Escape') {
        closeLoginModal();
    }
});
