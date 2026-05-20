/** 服务列表页和服务详情页 */
let statusRefreshTimer = null;

async function renderServicesPage() {
    const main = document.getElementById('main-content');
    updateNav('services');

    try {
        const services = await api.listServices();
        main.innerHTML = `
            <div class="page-header">
                <div>
                    <div class="page-title">服务管理</div>
                    <div class="page-subtitle">已注册 ${services.length} 个服务</div>
                </div>
                <button class="btn btn-primary" onclick="openCreateServiceModal()">+ 新建服务</button>
            </div>
            <div class="card-grid" id="service-cards">
                ${services.length === 0 ? `
                    <div class="empty-state" style="grid-column:1/-1">
                        <div class="icon">⚙️</div>
                        <div class="title">暂无服务</div>
                        <p>创建你的第一个服务来开始使用</p>
                    </div>
                ` : services.map(s => renderServiceCard(s)).join('')}
            </div>
        `;
    } catch (e) {
        main.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><div class="title">加载服务失败</div><p>${escapeHtml(e.message)}</p></div>`;
    }

    // 自动刷新状态
    if (statusRefreshTimer) clearInterval(statusRefreshTimer);
    statusRefreshTimer = setInterval(refreshServiceStatuses, 5000);
    currentCleanup = () => { if (statusRefreshTimer) clearInterval(statusRefreshTimer); };
}

function renderServiceCard(s) {
    const badgeClass = s.status === 'running' ? 'badge-running' :
                       s.status === 'failed' ? 'badge-failed' :
                       s.status === 'stopped' ? 'badge-stopped' : 'badge-unknown';
    const statusDot = s.status === 'running' ? '🟢' : s.status === 'failed' ? '🟠' : '⚪';
    const statusText = s.status === 'running' ? '运行中' : s.status === 'failed' ? '已失败' : s.status === 'stopped' ? '已停止' : s.status;

    return `
        <div class="card" data-service="${s.name}">
            <div class="service-card-header">
                <span class="service-name" onclick="navigate('services/${s.name}')">${escapeHtml(s.display_name || s.name)}</span>
                <span class="badge ${badgeClass}">${statusDot} ${statusText}</span>
            </div>
            <div class="service-desc">${escapeHtml(s.description || '暂无描述')}</div>
            <div class="service-card-actions">
                ${s.status === 'running' ?
                    `<button class="btn btn-danger btn-sm" onclick="stopService('${s.name}')">■ 停止</button>
                     <button class="btn btn-outline btn-sm" onclick="restartService('${s.name}')">↻ 重启</button>` :
                    `<button class="btn btn-success btn-sm" onclick="startService('${s.name}')">▶ 启动</button>`
                }
                <button class="btn btn-outline btn-sm" onclick="navigate('services/${s.name}')">详情</button>
                <button class="btn btn-outline btn-sm btn-icon" onclick="deleteService('${s.name}')" title="删除">🗑️</button>
            </div>
        </div>
    `;
}

async function refreshServiceStatuses() {
    try {
        const services = await api.listServices();
        services.forEach(s => {
            const card = document.querySelector(`[data-service="${s.name}"]`);
            if (card) {
                const badge = card.querySelector('.badge');
                const badgeClass = s.status === 'running' ? 'badge-running' :
                                   s.status === 'failed' ? 'badge-failed' :
                                   s.status === 'stopped' ? 'badge-stopped' : 'badge-unknown';
                const statusDot = s.status === 'running' ? '🟢' : s.status === 'failed' ? '🟠' : '⚪';
                const statusText = s.status === 'running' ? '运行中' : s.status === 'failed' ? '已失败' : s.status === 'stopped' ? '已停止' : s.status;
                if (badge) {
                    badge.className = `badge ${badgeClass}`;
                    badge.textContent = `${statusDot} ${statusText}`;
                }
            }
        });
    } catch (e) { /* 忽略 */ }
}

// ── 服务操作 ──────────────────────────────────────────
async function startService(name) {
    try {
        await api.startService(name);
        showToast(`服务 '${name}' 已启动`);
        renderServicesPage();
    } catch (e) { showToast(e.message, 'error'); }
}

async function stopService(name) {
    try {
        await api.stopService(name);
        showToast(`服务 '${name}' 已停止`);
        renderServicesPage();
    } catch (e) { showToast(e.message, 'error'); }
}

async function restartService(name) {
    try {
        await api.restartService(name);
        showToast(`服务 '${name}' 已重启`);
        renderServicesPage();
    } catch (e) { showToast(e.message, 'error'); }
}

async function deleteService(name) {
    if (!confirm(`确定要删除服务 '${name}' 吗？此操作不可恢复。`)) return;
    try {
        await api.deleteService(name);
        showToast(`服务 '${name}' 已删除`);
        renderServicesPage();
    } catch (e) { showToast(e.message, 'error'); }
}

// ── 创建服务弹窗 ─────────────────────────────────────
function openCreateServiceModal() {
    const overlay = document.getElementById('modal-overlay');
    overlay.innerHTML = `
        <div class="modal">
            <div class="modal-header">
                <span class="modal-title">新建服务</span>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label">服务名称 *</label>
                <input class="form-input" id="svc-name" placeholder="my-service" pattern="[a-zA-Z][a-zA-Z0-9_-]{0,63}">
            </div>
            <div class="form-group">
                <label class="form-label">显示名称</label>
                <input class="form-input" id="svc-display" placeholder="我的服务">
            </div>
            <div class="form-group">
                <label class="form-label">描述</label>
                <textarea class="form-textarea" id="svc-desc" placeholder="该服务的功能描述"></textarea>
            </div>
            <div class="form-group">
                <label class="form-label">代码来源</label>
                <div style="display:flex;gap:16px;margin-top:6px">
                    <label><input type="radio" name="code-source" value="editor" checked> 在线编辑</label>
                    <label><input type="radio" name="code-source" value="upload"> 上传文件</label>
                </div>
            </div>
            <div id="code-editor-section">
                <div class="form-group">
                    <label class="form-label">Python 代码</label>
                    <div class="editor-wrapper">
                        <textarea class="form-textarea" id="svc-code" rows="15" style="font-family:monospace;font-size:13px">${escapeHtml(getDefaultServiceCode())}</textarea>
                    </div>
                </div>
            </div>
            <div id="code-upload-section" style="display:none">
                <div class="form-group">
                    <label class="form-label">上传 Python 脚本</label>
                    <input type="file" id="svc-upload" accept=".py" class="form-input">
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-outline" onclick="closeModal()">取消</button>
                <button class="btn btn-primary" onclick="createService()">创建</button>
            </div>
        </div>
    `;
    overlay.classList.add('open');

    document.querySelectorAll('input[name="code-source"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            document.getElementById('code-editor-section').style.display = e.target.value === 'editor' ? 'block' : 'none';
            document.getElementById('code-upload-section').style.display = e.target.value === 'upload' ? 'block' : 'none';
        });
    });
}

function getDefaultServiceCode() {
    return `import time

def run(config, modules):
    """主入口函数。
    config: 来自 config.toml 的字典
    modules: 模块命名空间字典，例如 modules["module-name"].method()
    """
    interval = config.get("interval", {}).get("seconds", 5)
    message = config.get("message", {}).get("text", "Hello!")

    while True:
        print(f"[service] {message}")
        time.sleep(interval)

def on_config_reload(new_config):
    print("配置已重新加载")

def on_shutdown():
    print("服务正在关闭")
`;
}

async function createService() {
    const name = document.getElementById('svc-name').value.trim();
    if (!name) { showToast('请输入服务名称', 'error'); return; }

    const codeSource = document.querySelector('input[name="code-source"]:checked').value;
    let code = null;

    if (codeSource === 'editor') {
        code = document.getElementById('svc-code').value;
    }

    try {
        await api.createService({
            name,
            display_name: document.getElementById('svc-display').value.trim() || name,
            description: document.getElementById('svc-desc').value.trim(),
            code_source: codeSource,
            code,
        });

        // 如果是上传方式，单独处理
        if (codeSource === 'upload') {
            const fileInput = document.getElementById('svc-upload');
            if (fileInput.files[0]) {
                await api.uploadServiceScript(name, fileInput.files[0]);
            }
        }

        closeModal();
        showToast(`服务 '${name}' 已创建`);
        renderServicesPage();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// ── 服务详情页 ──────────────────────────────────────
let logWs = null;

async function renderServiceDetailPage(params) {
    const name = params.name;
    if (!name) { navigate('services'); return; }

    const main = document.getElementById('main-content');
    updateNav('services');

    try {
        const [service, serviceModules] = await Promise.all([
            api.getService(name),
            api.getServiceModules(name),
        ]);

        const badgeClass = service.status === 'running' ? 'badge-running' :
                           service.status === 'failed' ? 'badge-failed' :
                           service.status === 'stopped' ? 'badge-stopped' : 'badge-unknown';
        const statusText = service.status === 'running' ? '运行中' : service.status === 'failed' ? '已失败' : service.status === 'stopped' ? '已停止' : service.status;

        main.innerHTML = `
            <div class="page-header">
                <div>
                    <a href="#services" style="color:var(--text-secondary);text-decoration:none;font-size:13px">← 返回服务列表</a>
                    <div class="page-title" style="margin-top:8px">${escapeHtml(service.display_name || service.name)}</div>
                    <div class="page-subtitle">${escapeHtml(service.description || '')}</div>
                </div>
                <div style="display:flex;align-items:center;gap:12px">
                    <span class="badge ${badgeClass}">${statusText}</span>
                    ${service.status === 'running' ?
                        `<button class="btn btn-danger btn-sm" onclick="stopServiceDetail('${name}')">■ 停止</button>
                         <button class="btn btn-outline btn-sm" onclick="restartServiceDetail('${name}')">↻ 重启</button>` :
                        `<button class="btn btn-success btn-sm" onclick="startServiceDetail('${name}')">▶ 启动</button>`
                    }
                </div>
            </div>

            <div class="tabs">
                <div class="tab active" data-tab="info" onclick="switchTab(this)">基本信息</div>
                <div class="tab" data-tab="code" onclick="switchTab(this)">代码</div>
                <div class="tab" data-tab="config" onclick="switchTab(this)">配置</div>
                <div class="tab" data-tab="modules" onclick="switchTab(this)">模块</div>
                <div class="tab" data-tab="logs" onclick="switchTab(this)">日志</div>
            </div>

            <div class="tab-content active" id="tab-info">
                <ul class="info-list">
                    <li><span class="info-label">名称</span><span class="info-value">${escapeHtml(service.name)}</span></li>
                    <li><span class="info-label">状态</span><span class="info-value">${statusText}</span></li>
                    <li><span class="info-label">自动重启</span><span class="info-value">${service.auto_restart ? '是' : '否'}</span></li>
                    <li><span class="info-label">Python 路径</span><span class="info-value">${escapeHtml(service.python_path)}</span></li>
                    <li><span class="info-label">代码来源</span><span class="info-value">${service.code_source === 'editor' ? '在线编辑' : service.code_source === 'upload' ? '上传文件' : service.code_source}</span></li>
                    <li><span class="info-label">创建时间</span><span class="info-value">${new Date(service.created_at).toLocaleString()}</span></li>
                    <li><span class="info-label">更新时间</span><span class="info-value">${new Date(service.updated_at).toLocaleString()}</span></li>
                </ul>
            </div>

            <div class="tab-content" id="tab-code">
                <div class="editor-wrapper">
                    <div class="editor-toolbar">
                        <span>main.py</span>
                        <div>
                            <button class="btn btn-primary btn-sm" onclick="saveServiceCode('${name}')">保存</button>
                            <label class="btn btn-outline btn-sm" style="cursor:pointer">
                                上传 <input type="file" accept=".py" style="display:none" onchange="uploadServiceCode('${name}', this)">
                            </label>
                        </div>
                    </div>
                    <textarea id="service-code-editor" class="form-textarea" rows="20" style="font-family:monospace;font-size:13px;border:none;border-radius:0"></textarea>
                </div>
            </div>

            <div class="tab-content" id="tab-config">
                <div class="editor-wrapper">
                    <div class="editor-toolbar">
                        <span>config.toml</span>
                        <button class="btn btn-primary btn-sm" onclick="saveServiceConfig('${name}')">保存并热加载</button>
                    </div>
                    <textarea id="service-config-editor" class="form-textarea" rows="15" style="font-family:monospace;font-size:13px;border:none;border-radius:0"></textarea>
                </div>
                <div style="margin-top:8px;font-size:12px;color:var(--text-muted)">保存配置后，如果服务正在运行将自动触发热加载。</div>
            </div>

            <div class="tab-content" id="tab-modules">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
                    <div style="font-size:14px;color:var(--text-secondary)">选择要启用的模块，更改后需要重启服务生效。</div>
                    <div style="display:flex;gap:8px">
                        <button class="btn btn-primary btn-sm" onclick="saveServiceModules('${name}')">保存模块</button>
                        <button class="btn btn-outline btn-sm" onclick="restartServiceDetail('${name}')">重启服务</button>
                    </div>
                </div>
                <div id="service-modules-list">
                    ${serviceModules.modules.map(m => `
                        <div class="module-list-item" data-module-id="${m.module_id}">
                            <label class="toggle">
                                <input type="checkbox" ${m.enabled ? 'checked' : ''} data-module-id="${m.module_id}" class="module-toggle">
                                <span class="toggle-slider"></span>
                            </label>
                            <div class="module-info">
                                <div class="module-title">${escapeHtml(m.display_name)} <span class="module-version">v${m.version}</span></div>
                                <div class="module-desc-small">${escapeHtml(m.description || m.name)}</div>
                            </div>
                            <span class="module-order">#${m.load_order}</span>
                        </div>
                    `).join('')}
                </div>
                ${serviceModules.modules.length === 0 ? '<div class="empty-state"><div class="title">暂无可用模块</div><p>请先在模块管理页面创建模块</p></div>' : ''}
            </div>

            <div class="tab-content" id="tab-logs">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                    <span style="font-size:14px;color:var(--text-secondary)">实时日志流</span>
                    <div style="display:flex;gap:8px">
                        <button class="btn btn-outline btn-sm" onclick="clearLogViewer()">清空</button>
                        <button class="btn btn-outline btn-sm" id="btn-auto-scroll" onclick="toggleAutoScroll()">自动滚动: 开</button>
                    </div>
                </div>
                <div id="log-viewer" class="log-viewer"></div>
            </div>
        `;

        // 加载代码和配置
        loadServiceCode(name);
        loadServiceConfig(name);

        // 加载历史日志
        loadServiceLogs(name);

        // 连接 WebSocket 日志
        connectLogWs(name);

    } catch (e) {
        main.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><div class="title">出错了</div><p>${escapeHtml(e.message)}</p></div>`;
    }

    currentCleanup = () => {
        if (logWs) { logWs.close(); logWs = null; }
        if (statusRefreshTimer) clearInterval(statusRefreshTimer);
    };
}

function switchTab(el) {
    const tabName = el.dataset.tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    document.getElementById(`tab-${tabName}`).classList.add('active');

    // 切换到日志标签时连接 WebSocket
    if (tabName === 'logs' && !logWs) {
        const name = window.location.hash.split('/')[1];
        if (name) connectLogWs(name);
    }
}

// ── 服务详情操作 ───────────────────────────────────
async function startServiceDetail(name) {
    try { await api.startService(name); showToast('服务已启动'); renderServiceDetailPage({name}); }
    catch (e) { showToast(e.message, 'error'); }
}

async function stopServiceDetail(name) {
    try { await api.stopService(name); showToast('服务已停止'); renderServiceDetailPage({name}); }
    catch (e) { showToast(e.message, 'error'); }
}

async function restartServiceDetail(name) {
    try { await api.restartService(name); showToast('服务已重启'); renderServiceDetailPage({name}); }
    catch (e) { showToast(e.message, 'error'); }
}

// ── 代码编辑 ─────────────────────────────────────────
async function loadServiceCode(name) {
    try {
        const data = await api.getServiceCode(name);
        const editor = document.getElementById('service-code-editor');
        if (editor) editor.value = data.code;
    } catch (e) { /* 忽略 */ }
}

async function saveServiceCode(name) {
    const code = document.getElementById('service-code-editor').value;
    try {
        const result = await api.updateServiceCode(name, code);
        showToast(result.message + (result.needs_restart ? ' - 需要重启' : ''));
    } catch (e) { showToast(e.message, 'error'); }
}

async function uploadServiceCode(name, input) {
    if (input.files[0]) {
        try {
            const result = await api.uploadServiceScript(name, input.files[0]);
            showToast(result.message);
            loadServiceCode(name);
        } catch (e) { showToast(e.message, 'error'); }
    }
}

// ── 配置编辑 ──────────────────────────────────────────
async function loadServiceConfig(name) {
    try {
        const data = await api.getServiceConfig(name);
        const editor = document.getElementById('service-config-editor');
        if (editor) editor.value = data.config;
    } catch (e) { /* 忽略 */ }
}

async function saveServiceConfig(name) {
    const config = document.getElementById('service-config-editor').value;
    try {
        const result = await api.updateServiceConfig(name, config);
        showToast('配置已保存' + (result.hot_reloaded ? '并已热加载' : ''));
    } catch (e) { showToast(e.message, 'error'); }
}

// ── 模块绑定 ─────────────────────────────────────────
async function saveServiceModules(name) {
    const items = document.querySelectorAll('#service-modules-list .module-list-item');
    const modules = [];
    let order = 0;
    items.forEach(item => {
        const moduleId = parseInt(item.dataset.moduleId);
        const toggle = item.querySelector('.module-toggle');
        if (toggle && toggle.checked) {
            modules.push({ module_id: moduleId, enabled: true, load_order: order++ });
        } else {
            modules.push({ module_id: moduleId, enabled: false, load_order: order++ });
        }
    });

    try {
        const result = await api.updateServiceModules(name, modules);
        showToast(`模块已更新${result.restart_required ? ' - 需要重启服务' : ''}`);
    } catch (e) { showToast(e.message, 'error'); }
}

// ── 日志 ─────────────────────────────────────────────
let autoScroll = true;

async function loadServiceLogs(name) {
    try {
        const data = await api.getServiceLogs(name, 200);
        const viewer = document.getElementById('log-viewer');
        if (viewer && data.logs) {
            viewer.innerHTML = data.logs.map(l => `<div class="log-line">${escapeHtml(l)}</div>`).join('');
            if (autoScroll) viewer.scrollTop = viewer.scrollHeight;
        }
    } catch (e) { /* 忽略 */ }
}

function connectLogWs(name) {
    if (logWs) { logWs.close(); }
    try {
        logWs = new WebSocket(api.getLogWsUrl(name));
        logWs.onmessage = (event) => {
            const viewer = document.getElementById('log-viewer');
            if (viewer) {
                const line = document.createElement('div');
                line.className = 'log-line';
                line.textContent = event.data;
                viewer.appendChild(line);
                if (autoScroll) viewer.scrollTop = viewer.scrollHeight;
            }
        };
        logWs.onerror = () => { /* WebSocket 错误在服务未运行时属于正常情况 */ };
    } catch (e) { /* 忽略 */ }
}

function clearLogViewer() {
    const viewer = document.getElementById('log-viewer');
    if (viewer) viewer.innerHTML = '';
}

function toggleAutoScroll() {
    autoScroll = !autoScroll;
    const btn = document.getElementById('btn-auto-scroll');
    if (btn) btn.textContent = `自动滚动: ${autoScroll ? '开' : '关'}`;
}

// ── 弹窗辅助 ────────────────────────────────────────
function closeModal() {
    document.getElementById('modal-overlay').classList.remove('open');
    document.getElementById('modal-overlay').innerHTML = '';
}
