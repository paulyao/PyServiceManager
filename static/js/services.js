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
async function openCreateServiceModal() {
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
                        <div id="svc-code" class="cm-editor-container"></div>
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

    const CM = await window.CMReady;
    CM.createPythonEditor('svc-code', { initialValue: getDefaultServiceCode(), height: '330px' });

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
        const CM = await window.CMReady;
        code = CM.getEditorValue('svc-code');
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
                if (!fileInput.files[0].name.endsWith('.py')) {
                    showToast('只能上传 .py 文件', 'error'); return;
                }
                await api.uploadServiceScript(name, fileInput.files[0]);
            } else {
                showToast('请选择要上传的文件', 'error'); return;
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
                <div class="tab" data-tab="deps" onclick="switchTab(this)">依赖</div>
                <div class="tab" data-tab="logs" onclick="switchTab(this)">日志</div>
            </div>

            <div class="tab-content active" id="tab-info">
                <div class="info-form">
                    <div class="info-form-section">
                        <div class="info-form-title">基本属性</div>
                        <ul class="info-list">
                            <li><span class="info-label">名称</span><span class="info-value">${escapeHtml(service.name)}</span></li>
                            <li><span class="info-label">状态</span><span class="info-value">${statusText}</span></li>
                            <li><span class="info-label">Python 路径</span><span class="info-value">${escapeHtml(service.python_path)}</span></li>
                            <li><span class="info-label">代码来源</span><span class="info-value">${service.code_source === 'editor' ? '在线编辑' : service.code_source === 'upload' ? '上传文件' : service.code_source}</span></li>
                            <li><span class="info-label">创建时间</span><span class="info-value">${new Date(service.created_at).toLocaleString()}</span></li>
                            <li><span class="info-label">更新时间</span><span class="info-value">${new Date(service.updated_at).toLocaleString()}</span></li>
                        </ul>
                    </div>
                    <div class="info-form-section">
                        <div class="info-form-title">可编辑属性</div>
                        <div class="info-editable-fields">
                            <div class="info-field">
                                <label class="info-label">随系统启动</label>
                                <label class="toggle">
                                    <input type="checkbox" id="svc-edit-enabled" ${service.enabled ? 'checked' : ''}>
                                    <span class="toggle-slider"></span>
                                </label>
                            </div>
                            <div class="info-field">
                                <label class="info-label" for="svc-edit-description">服务描述</label>
                                <textarea class="form-textarea" id="svc-edit-description" rows="2" placeholder="描述该服务的功能">${escapeHtml(service.description || '')}</textarea>
                            </div>
                            <div class="info-field">
                                <label class="info-label" for="svc-edit-remarks">备注</label>
                                <textarea class="form-textarea" id="svc-edit-remarks" rows="3" placeholder="备注信息">${escapeHtml(service.remarks || '')}</textarea>
                            </div>
                            <div style="margin-top:12px">
                                <button class="btn btn-primary btn-sm" onclick="saveServiceInfo('${name}')">保存</button>
                            </div>
                        </div>
                    </div>
                </div>
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
                    <div id="service-code-editor" class="cm-editor-container"></div>
                </div>
            </div>

            <div class="tab-content" id="tab-config">
                <div class="editor-wrapper">
                    <div class="editor-toolbar">
                        <span>config.toml</span>
                        <button class="btn btn-primary btn-sm" onclick="saveServiceConfig('${name}')">保存并热加载</button>
                    </div>
                    <div id="service-config-editor" class="cm-editor-container"></div>
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

            <div class="tab-content" id="tab-deps">
                <div class="dep-section">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
                        <div style="font-size:14px;color:var(--text-secondary)">服务自身依赖声明</div>
                        <div style="display:flex;gap:8px">
                            <button class="btn btn-primary btn-sm" onclick="saveServiceRequirements('${name}')">保存</button>
                        </div>
                    </div>
                    <textarea class="form-textarea" id="svc-requirements" rows="3" placeholder="每行一个依赖，如 requests>=2.28">${escapeHtml((service.requirements || []).join('\n'))}</textarea>
                </div>
                <div id="svc-deps-scan-section" style="margin-top:20px"></div>
                <div id="svc-deps-module-section" style="margin-top:20px"></div>
                <div id="svc-deps-comparison" style="margin-top:16px"></div>
                <div id="svc-deps-summary" style="margin-top:16px"></div>
                <div style="margin-top:16px;display:flex;gap:8px;justify-content:center">
                    <button class="btn btn-outline" onclick="loadServiceDeps('${name}')">检查所有依赖</button>
                    <button class="btn btn-primary" id="btn-install-svc-deps" onclick="installServiceDeps('${name}')">一键安装缺失依赖</button>
                </div>
            </div>
        `;

        // 初始化 CodeMirror 编辑器
        const CM = await window.CMReady;
        CM.createPythonEditor('service-code-editor');
        CM.createTomlEditor('service-config-editor');

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

    currentCleanup = async () => {
        if (logWs) { logWs.close(); logWs = null; }
        if (statusRefreshTimer) clearInterval(statusRefreshTimer);
        try {
            const CM = await window.CMReady;
            CM.destroyEditor('service-code-editor');
            CM.destroyEditor('service-config-editor');
        } catch (e) { /* 忽略 */ }
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

    // 切换到依赖标签时自动加载依赖状态
    if (tabName === 'deps') {
        const name = window.location.hash.split('/')[1];
        if (name) loadServiceDeps(name);
    }
}

// ── 依赖管理 ─────────────────────────────────────────

function _renderScanImports(imports) {
    const stdlibImports = imports.filter(i => i.import_type === 'stdlib');
    const thirdPartyImports = imports.filter(i => i.import_type === 'third_party');
    const localImports = imports.filter(i => i.import_type === 'local');

    let html = '';

    // Third-party imports (always shown)
    thirdPartyImports.forEach(imp => {
        const pipDisplay = imp.pip_name && imp.pip_name !== imp.module_name
            ? ` <span style="color:var(--text-muted);font-size:11px">&rarr; ${escapeHtml(imp.pip_name)}</span>` : '';
        html += `<div class="scan-import-item">
            <span class="scan-import-name">${escapeHtml(imp.full_path)}${pipDisplay}</span>
            <span class="scan-tag scan-tag-third-party">第三方</span>
        </div>`;
    });

    // Local imports
    localImports.forEach(imp => {
        html += `<div class="scan-import-item">
            <span class="scan-import-name">${escapeHtml(imp.full_path)}</span>
            <span class="scan-tag scan-tag-local">本地</span>
        </div>`;
    });

    // Stdlib imports (collapsible)
    if (stdlibImports.length > 0) {
        html += `<div class="scan-stdlib-toggle" onclick="var n=this.nextElementSibling;n.style.display=n.style.display==='none'?'block':'none';this.querySelector('.scan-stdlib-arrow').textContent=n.style.display==='none'?'▸':'▾'">
            <span class="scan-stdlib-arrow">▸</span> ${stdlibImports.length} 个标准库 import
        </div>
        <div class="scan-stdlib-list" style="display:none">
            ${stdlibImports.map(imp => `<div class="scan-import-item">
                <span class="scan-import-name">${escapeHtml(imp.full_path)}</span>
                <span class="scan-tag scan-tag-stdlib">标准库</span>
            </div>`).join('')}
        </div>`;
    }

    return html || '<div style="color:var(--text-muted);font-size:12px">无 import 语句</div>';
}

function _renderScanSection(scannedImports) {
    if (!scannedImports) return '';

    let html = '<div style="font-size:14px;color:var(--text-secondary);margin-bottom:8px">代码扫描结果</div>';

    // Service scan
    const svcScan = scannedImports.service_scan;
    if (svcScan) {
        if (svcScan.error) {
            html += `<div class="scan-file-group"><div class="scan-file-header">main.py</div><div style="color:var(--danger);font-size:12px">${escapeHtml(svcScan.error)}</div></div>`;
        } else {
            html += `<div class="scan-file-group">
                <div class="scan-file-header">main.py <span style="font-size:11px;color:var(--text-muted)">(${svcScan.third_party_packages.length} 个第三方依赖)</span></div>
                ${_renderScanImports(svcScan.imports)}
            </div>`;
        }
    }

    // Module scans
    if (scannedImports.module_scans && scannedImports.module_scans.length > 0) {
        scannedImports.module_scans.forEach(modScan => {
            const scan = modScan.scan;
            if (scan.error) {
                html += `<div class="scan-file-group"><div class="scan-file-header">${escapeHtml(modScan.module_display_name)}</div><div style="color:var(--danger);font-size:12px">${escapeHtml(scan.error)}</div></div>`;
            } else {
                html += `<div class="scan-file-group">
                    <div class="scan-file-header">
                        <span>${escapeHtml(modScan.module_display_name)}</span>
                        <span style="font-size:11px;color:var(--text-muted)">${escapeHtml(modScan.module_name)} · ${scan.third_party_packages.length} 个第三方依赖</span>
                    </div>
                    ${_renderScanImports(scan.imports)}
                </div>`;
            }
        });
    }

    return html;
}

function _renderScanComparison(comparison) {
    if (!comparison) return '';

    const hasContent = comparison.matched.length > 0 || comparison.scanned_only.length > 0 || comparison.declared_only.length > 0;
    if (!hasContent) return '';

    let html = '<div style="font-size:14px;color:var(--text-secondary);margin-bottom:8px">声明 vs 代码扫描对比</div><div class="scan-comparison">';

    if (comparison.matched.length > 0) {
        html += comparison.matched.map(p =>
            `<div class="scan-cmp-item scan-cmp-matched"><span class="scan-cmp-icon">&#10003;</span> ${escapeHtml(p)} <span class="scan-cmp-label">已声明 & 代码中有</span></div>`
        ).join('');
    }

    if (comparison.scanned_only.length > 0) {
        html += comparison.scanned_only.map(p =>
            `<div class="scan-cmp-item scan-cmp-scanned-only"><span class="scan-cmp-icon">&#9888;</span> ${escapeHtml(p)} <span class="scan-cmp-label">代码中有但未声明</span> <button class="scan-add-btn" onclick="addScanPkgToRequirements('${escapeHtml(p)}')">+ 添加</button></div>`
        ).join('');
    }

    if (comparison.declared_only.length > 0) {
        html += comparison.declared_only.map(p =>
            `<div class="scan-cmp-item scan-cmp-declared-only"><span class="scan-cmp-icon">&#8505;</span> ${escapeHtml(p)} <span class="scan-cmp-label">已声明但代码中未使用</span></div>`
        ).join('');
    }

    html += '</div>';
    return html;
}

function addScanPkgToRequirements(pkgName) {
    const textarea = document.getElementById('svc-requirements');
    if (!textarea) return;
    const current = textarea.value.trim();
    textarea.value = current ? current + '\n' + pkgName : pkgName;
    showToast(`已添加 ${pkgName} 到依赖声明，请保存`);
}

async function loadServiceDeps(name) {
    try {
        const result = await api.getServiceDeps(name, true);
        const scanSection = document.getElementById('svc-deps-scan-section');
        const modSection = document.getElementById('svc-deps-module-section');
        const comparisonSection = document.getElementById('svc-deps-comparison');
        const summary = document.getElementById('svc-deps-summary');

        // Render scan results
        if (scanSection && result.scanned_imports) {
            scanSection.innerHTML = _renderScanSection(result.scanned_imports);
        }

        // Render module deps
        let modHtml = '';
        if (result.module_requirements && result.module_requirements.length > 0) {
            modHtml = result.module_requirements.map(mod => {
                const modAllOk = mod.requirements.every(r => r.satisfied);
                return `
                    <div class="dep-module-group">
                        <div class="dep-module-header">
                            <span style="font-weight:500">${escapeHtml(mod.module_display_name)}</span>
                            <span style="font-size:12px;color:var(--text-muted)">${escapeHtml(mod.module_name)}</span>
                            ${modAllOk ? '<span class="dep-status-badge dep-satisfied">✓ 全部满足</span>' : '<span class="dep-status-badge dep-missing">✗ 有缺失</span>'}
                        </div>
                        ${_renderDepsStatusItems(mod.requirements)}
                    </div>
                `;
            }).join('');
        } else {
            modHtml = '<div style="color:var(--text-muted);font-size:13px">无已启用模块依赖</div>';
        }
        if (modSection) modSection.innerHTML = `
            <div style="font-size:14px;color:var(--text-secondary);margin-bottom:8px">模块依赖</div>
            ${modHtml}
        `;

        // Render scan comparison
        if (comparisonSection && result.scan_comparison) {
            comparisonSection.innerHTML = _renderScanComparison(result.scan_comparison);
        }

        // Render summary
        const svcCount = result.service_requirements ? result.service_requirements.length : 0;
        const totalCount = result.all_requirements ? result.all_requirements.length : 0;
        const satisfiedCount = result.all_requirements ? result.all_requirements.filter(r => r.satisfied).length : 0;
        const scannedThirdParty = result.scanned_imports ? result.scanned_imports.all_third_party.length : 0;
        if (summary) summary.innerHTML = `
            <div class="dep-summary">
                <div style="display:flex;gap:24px;justify-content:center;font-size:14px">
                    <div><strong>${totalCount}</strong> 总依赖</div>
                    <div style="color:var(--success)"><strong>${satisfiedCount}</strong> 已满足</div>
                    <div style="color:${result.all_satisfied ? 'var(--success)' : 'var(--danger)'}"><strong>${result.missing_count}</strong> ${result.all_satisfied ? '缺失' : '未满足'}</div>
                    ${scannedThirdParty > 0 ? `<div style="color:var(--primary)"><strong>${scannedThirdParty}</strong> 代码扫描发现</div>` : ''}
                </div>
                ${result.all_satisfied ? '<div style="text-align:center;margin-top:8px;color:var(--success);font-size:13px">✓ 所有依赖已满足，服务可正常运行</div>' : ''}
            </div>
        `;
    } catch (e) { showToast(e.message, 'error'); }
}

async function installServiceDeps(name) {
    const btn = document.getElementById('btn-install-svc-deps');
    if (btn) { btn.disabled = true; btn.textContent = '安装中...'; }
    try {
        const result = await api.installServiceDeps(name);
        if (result.success) {
            showToast(`已安装 ${result.installed.length} 个依赖`);
        } else {
            // Build detailed error message
            let msg = '安装失败: ';
            const failedDetails = result.failed.map(pkg => {
                const err = result.errors && result.errors[pkg];
                return err ? `${pkg} (${err})` : pkg;
            });
            msg += failedDetails.join(', ');
            // Also mention successful installs
            if (result.installed && result.installed.length > 0) {
                msg += ` (已成功安装: ${result.installed.join(', ')})`;
            }
            showToast(msg, 'error', 8000);
        }
        await loadServiceDeps(name);
    } catch (e) { showToast(e.message, 'error'); }
    if (btn) { btn.disabled = false; btn.textContent = '一键安装缺失依赖'; }
}

async function saveServiceRequirements(name) {
    const textarea = document.getElementById('svc-requirements');
    if (!textarea) return;
    const reqs = textarea.value.split('\n').map(r => r.trim()).filter(r => r.length > 0);
    try {
        await api.updateServiceRequirements(name, reqs);
        showToast('依赖声明已保存');
        await loadServiceDeps(name);
    } catch (e) { showToast(e.message, 'error'); }
}

// ── 服务详情操作 ───────────────────────────────────
async function saveServiceInfo(name) {
    const enabled = document.getElementById('svc-edit-enabled').checked;
    const description = document.getElementById('svc-edit-description').value.trim();
    const remarks = document.getElementById('svc-edit-remarks').value.trim();

    try {
        await api.updateService(name, {
            enabled,
            description: description || null,
            remarks: remarks || null,
        });
        showToast('属性已保存');
        renderServiceDetailPage({name});
    } catch (e) { showToast(e.message, 'error'); }
}

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
        const CM = await window.CMReady;
        CM.setEditorValue('service-code-editor', data.code);
    } catch (e) { /* 忽略 */ }
}

async function saveServiceCode(name) {
    const CM = await window.CMReady;
    const code = CM.getEditorValue('service-code-editor');
    try {
        const result = await api.updateServiceCode(name, code);
        showToast(result.message + (result.needs_restart ? ' - 需要重启' : ''));
    } catch (e) { showToast(e.message, 'error'); }
}

async function uploadServiceCode(name, input) {
    if (input.files[0]) {
        if (!input.files[0].name.endsWith('.py')) {
            showToast('只能上传 .py 文件', 'error');
            input.value = '';
            return;
        }
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
        const CM = await window.CMReady;
        CM.setEditorValue('service-config-editor', data.config);
    } catch (e) { /* 忽略 */ }
}

async function saveServiceConfig(name) {
    const CM = await window.CMReady;
    const config = CM.getEditorValue('service-config-editor');
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
function getLogLevelClass(text) {
    if (text.includes('[ERROR]')) return 'log-line log-error';
    if (text.includes('[WARN]')) return 'log-line log-warn';
    if (text.includes('[INFO]')) return 'log-line log-info';
    return 'log-line';
}

let autoScroll = true;

async function loadServiceLogs(name) {
    try {
        const data = await api.getServiceLogs(name, 200);
        const viewer = document.getElementById('log-viewer');
        if (viewer && data.logs) {
            viewer.innerHTML = data.logs.map(l => `<div class="${getLogLevelClass(l)}">${escapeHtml(l)}</div>`).join('');
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
                line.className = getLogLevelClass(event.data);
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
async function closeModal() {
    // 先销毁编辑器，再清空 DOM
    try {
        const CM = await window.CMReady;
        CM.destroyAllEditors();
    } catch (e) { /* 忽略 */ }
    document.getElementById('modal-overlay').classList.remove('open');
    document.getElementById('modal-overlay').innerHTML = '';
}
