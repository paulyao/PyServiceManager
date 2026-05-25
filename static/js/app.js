/** 主应用：路由、布局、通用工具 */

// ── Toast 通知 ──────────────────────────────────────
function showToast(message, type = 'success', duration = 3000) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icons = { success: '✓', error: '✗', warning: '⚠' };
    toast.innerHTML = `<span>${icons[type] || ''}</span> ${escapeHtml(message)}`;
    container.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, duration);
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

// ── 路由 ───────────────────────────────────────────
const routes = {
    '': renderServicesPage,
    'services': renderServicesPage,
    'services/:name': renderServiceDetailPage,
    'modules': renderModulesPage,
    'backup': renderBackupPage,
};

let currentCleanup = null;

function navigate(hash) {
    if (currentCleanup) { currentCleanup(); currentCleanup = null; }
    window.location.hash = hash;
}

function getRoute() {
    const hash = window.location.hash.slice(1) || '';
    const parts = hash.split('/');

    // 先尝试精确匹配
    if (routes[hash]) return { handler: routes[hash], params: {} };

    // 尝试模式匹配
    for (const pattern in routes) {
        const patternParts = pattern.split('/');
        if (patternParts.length !== parts.length) continue;
        const params = {};
        let match = true;
        for (let i = 0; i < patternParts.length; i++) {
            if (patternParts[i].startsWith(':')) {
                params[patternParts[i].slice(1)] = parts[i];
            } else if (patternParts[i] !== parts[i]) {
                match = false; break;
            }
        }
        if (match) return { handler: routes[pattern], params };
    }
    return { handler: renderServicesPage, params: {} };
}

function handleRoute() {
    const { handler, params } = getRoute();
    handler(params);
}

window.addEventListener('hashchange', handleRoute);

// ── 导航激活状态 ─────────────────────────────────────
function updateNav(hash) {
    document.querySelectorAll('.nav-item').forEach(el => {
        const navHash = el.dataset.route || '';
        el.classList.toggle('active', hash.startsWith(navHash));
    });
}

// ── 初始化 ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    handleRoute();
});
