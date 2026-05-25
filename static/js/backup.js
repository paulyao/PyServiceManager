/** Backup & Restore page */

// ── State ──────────────────────────────────────────
let backupItems = null;
let selectedModules = new Set();
let selectedServices = new Set();
let includeBindings = true;
let backupFile = null;
let previewData = null;
let isRestoring = false;

// ── Main Render ────────────────────────────────────
async function renderBackupPage() {
    const main = document.getElementById('main-content');
    updateNav('backup');

    main.innerHTML = `
        <div class="page-header">
            <h1>备份恢复</h1>
            <p style="color:var(--text-muted);margin-top:4px;">数据备份与跨机器迁移工具</p>
        </div>
        <div class="backup-layout">
            <div class="backup-section" id="backup-create-section">
                <div class="backup-section-title">
                    <span class="icon">💾</span> 创建备份
                </div>
                <div id="backup-items-container">
                    <div class="empty-state" style="padding:20px"><div class="title">加载中...</div></div>
                </div>
            </div>
            <div class="backup-section" id="backup-restore-section">
                <div class="backup-section-title">
                    <span class="icon">📂</span> 恢复备份
                </div>
                <div id="restore-upload-area"></div>
                <div id="restore-preview-area"></div>
                <div id="restore-conflict-area"></div>
                <div id="restore-result-area"></div>
            </div>
        </div>
    `;

    // Load backup items
    try {
        backupItems = await api.getBackupItems();
        renderBackupItems();
    } catch (e) {
        document.getElementById('backup-items-container').innerHTML =
            `<div class="backup-empty">加载失败: ${escapeHtml(e.message)}</div>`;
    }

    renderRestoreUpload();
}

// ── Backup: Items List ─────────────────────────────
function renderBackupItems() {
    const container = document.getElementById('backup-items-container');
    if (!backupItems) return;

    const moduleCount = backupItems.modules.length;
    const serviceCount = backupItems.services.length;

    let html = '';

    // Modules
    html += `<div class="backup-item-group">
        <div class="backup-item-group-header">
            <span class="backup-item-group-label">模块</span>
            <span class="backup-item-group-count">${moduleCount} 个可备份</span>
        </div>`;
    if (moduleCount === 0) {
        html += `<div class="backup-empty">无非内置模块</div>`;
    } else {
        html += `<div style="margin-bottom:6px">
            <label class="backup-option" style="padding:4px 0;font-weight:600">
                <input type="checkbox" id="select-all-modules" onchange="toggleAllModules(this.checked)">
                全选模块
            </label>
        </div>
        <div class="backup-item-list">`;
        for (const m of backupItems.modules) {
            html += `<label class="backup-item">
                <input type="checkbox" value="${escapeHtml(m.name)}" data-type="module" onchange="toggleModule('${escapeHtml(m.name)}', this.checked)">
                <span class="backup-item-name">${escapeHtml(m.display_name || m.name)}</span>
                <span class="backup-item-detail">${escapeHtml(m.name)}</span>
            </label>`;
        }
        html += `</div>`;
    }
    html += `</div>`;

    // Services
    html += `<div class="backup-item-group">
        <div class="backup-item-group-header">
            <span class="backup-item-group-label">服务</span>
            <span class="backup-item-group-count">${serviceCount} 个可备份</span>
        </div>`;
    if (serviceCount === 0) {
        html += `<div class="backup-empty">无服务</div>`;
    } else {
        html += `<div style="margin-bottom:6px">
            <label class="backup-option" style="padding:4px 0;font-weight:600">
                <input type="checkbox" id="select-all-services" onchange="toggleAllServices(this.checked)">
                全选服务
            </label>
        </div>
        <div class="backup-item-list">`;
        for (const s of backupItems.services) {
            const statusIcon = s.status === 'running' ? '🟢' : s.status === 'failed' ? '🔴' : '⚪';
            html += `<label class="backup-item">
                <input type="checkbox" value="${escapeHtml(s.name)}" data-type="service" onchange="toggleService('${escapeHtml(s.name)}', this.checked)">
                <span class="backup-item-name">${escapeHtml(s.display_name || s.name)}</span>
                <span class="backup-item-detail">${statusIcon} ${escapeHtml(s.name)}</span>
            </label>`;
        }
        html += `</div>`;
    }
    html += `</div>`;

    // Options
    html += `<div class="backup-option">
        <input type="checkbox" id="include-bindings" checked onchange="includeBindings=this.checked">
        <span>包含服务-模块绑定关系</span>
    </div>`;

    // Actions
    html += `<div class="backup-actions">
        <button class="btn btn-primary" onclick="createFullBackup()">一键全量备份</button>
        <button class="btn btn-secondary" onclick="createSelectiveBackup()">选择性备份</button>
    </div>`;

    container.innerHTML = html;
}

function toggleAllModules(checked) {
    selectedModules = checked ? new Set(backupItems.modules.map(m => m.name)) : new Set();
    document.querySelectorAll('input[data-type="module"]').forEach(el => el.checked = checked);
}

function toggleAllServices(checked) {
    selectedServices = checked ? new Set(backupItems.services.map(s => s.name)) : new Set();
    document.querySelectorAll('input[data-type="service"]').forEach(el => el.checked = checked);
}

function toggleModule(name, checked) {
    if (checked) selectedModules.add(name); else selectedModules.delete(name);
    const allBox = document.getElementById('select-all-modules');
    if (allBox) allBox.checked = selectedModules.size === backupItems.modules.length;
}

function toggleService(name, checked) {
    if (checked) selectedServices.add(name); else selectedServices.delete(name);
    const allBox = document.getElementById('select-all-services');
    if (allBox) allBox.checked = selectedServices.size === backupItems.services.length;
}

// ── Backup: Create ─────────────────────────────────
async function createFullBackup() {
    await doCreateBackup(null, null);
}

async function createSelectiveBackup() {
    if (selectedModules.size === 0 && selectedServices.size === 0) {
        showToast('请至少选择一个模块或服务', 'warning');
        return;
    }
    await doCreateBackup(
        selectedModules.size > 0 ? [...selectedModules] : [],
        selectedServices.size > 0 ? [...selectedServices] : []
    );
}

async function doCreateBackup(moduleNames, serviceNames) {
    const data = {
        modules: moduleNames,
        services: serviceNames,
        include_bindings: includeBindings,
    };
    try {
        const result = await api.createBackup(data);
        // Trigger download
        const url = URL.createObjectURL(result.blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = result.filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast(`备份已下载: ${result.filename}`, 'success');
    } catch (e) {
        showToast(`备份失败: ${e.message}`, 'error');
    }
}

// ── Restore: Upload ────────────────────────────────
function renderRestoreUpload() {
    const area = document.getElementById('restore-upload-area');
    area.innerHTML = `
        <div class="backup-upload-area" id="drop-zone" onclick="document.getElementById('backup-file-input').click()">
            <div class="backup-upload-icon">📁</div>
            <div class="backup-upload-text">点击或拖拽 <strong>.zip</strong> 备份文件到此处</div>
        </div>
        <input type="file" id="backup-file-input" accept=".zip" style="display:none" onchange="handleBackupFileSelect(this)">
        <div id="backup-file-info-area"></div>
        <div id="backup-preview-btn-area"></div>
    `;

    // Drag & drop
    const dropZone = document.getElementById('drop-zone');
    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', e => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            const input = document.getElementById('backup-file-input');
            input.files = e.dataTransfer.files;
            handleBackupFileSelect(input);
        }
    });
}

function handleBackupFileSelect(input) {
    const file = input.files[0];
    if (!file) return;
    if (!file.name.endsWith('.zip')) {
        showToast('请选择 .zip 格式的备份文件', 'warning');
        return;
    }

    backupFile = file;
    previewData = null;

    // Show file info
    document.getElementById('backup-file-info-area').innerHTML = `
        <div class="backup-file-info">
            <span>📦</span>
            <span class="backup-file-name">${escapeHtml(file.name)} (${(file.size / 1024).toFixed(1)} KB)</span>
            <span class="backup-file-remove" onclick="clearBackupFile()">✕</span>
        </div>
    `;
    document.getElementById('backup-preview-btn-area').innerHTML = `
        <button class="btn btn-primary" style="width:100%" onclick="previewBackupFile()">预览备份内容</button>
    `;
    document.getElementById('restore-preview-area').innerHTML = '';
    document.getElementById('restore-conflict-area').innerHTML = '';
    document.getElementById('restore-result-area').innerHTML = '';
}

function clearBackupFile() {
    backupFile = null;
    previewData = null;
    document.getElementById('backup-file-input').value = '';
    document.getElementById('backup-file-info-area').innerHTML = '';
    document.getElementById('backup-preview-btn-area').innerHTML = '';
    document.getElementById('restore-preview-area').innerHTML = '';
    document.getElementById('restore-conflict-area').innerHTML = '';
    document.getElementById('restore-result-area').innerHTML = '';
}

// ── Restore: Preview ───────────────────────────────
async function previewBackupFile() {
    if (!backupFile) return;

    const btn = document.querySelector('#backup-preview-btn-area .btn');
    if (btn) { btn.disabled = true; btn.textContent = '分析中...'; }

    try {
        previewData = await api.previewBackup(backupFile);
        renderPreviewResult(previewData);
        renderConflictPanel(previewData);
    } catch (e) {
        showToast(`预览失败: ${e.message}`, 'error');
        document.getElementById('restore-preview-area').innerHTML =
            `<div class="backup-empty">预览失败: ${escapeHtml(e.message)}</div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '预览备份内容'; }
    }
}

function renderPreviewResult(data) {
    const area = document.getElementById('restore-preview-area');
    const m = data.manifest;
    const c = data.conflicts;

    const moduleConflicts = c.modules.filter(x => x.conflict_type !== 'builtin').length;
    const serviceConflicts = c.services.length;

    area.innerHTML = `
        <div class="backup-preview-summary">
            <div class="backup-preview-stat">
                <span>📦</span>
                <div><div class="count">${m.modules.length}</div>模块</div>
            </div>
            <div class="backup-preview-stat">
                <span>🔧</span>
                <div><div class="count">${m.services.length}</div>服务</div>
            </div>
            <div class="backup-preview-stat ${moduleConflicts > 0 ? 'conflict' : ''}">
                <span>⚠️</span>
                <div><div class="count">${moduleConflicts}</div>模块冲突</div>
            </div>
            <div class="backup-preview-stat ${serviceConflicts > 0 ? 'conflict' : ''}">
                <span>⚠️</span>
                <div><div class="count">${serviceConflicts}</div>服务冲突</div>
            </div>
        </div>
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px">
            来源: ${escapeHtml(m.source_host)} | 创建时间: ${escapeHtml(m.created_at)}
        </div>
    `;
}

function renderConflictPanel(data) {
    const area = document.getElementById('restore-conflict-area');
    const c = data.conflicts;

    const moduleConflicts = c.modules.filter(x => x.conflict_type !== 'builtin');
    const serviceConflicts = c.services;
    const hasConflicts = moduleConflicts.length > 0 || serviceConflicts.length > 0;

    let html = '';

    if (c.missing_modules.length > 0) {
        html += `<div style="padding:10px 14px;background:var(--danger-muted);border-radius:8px;margin-bottom:12px;font-size:13px;color:var(--danger)">
            以下绑定引用的模块在备份和本地均不存在: ${c.missing_modules.map(x => escapeHtml(x)).join(', ')}
        </div>`;
    }

    if (!hasConflicts) {
        html += `<button class="btn btn-primary" style="width:100%" onclick="executeRestore()">执行恢复</button>`;
    } else {
        html += `<div class="conflict-panel">`;
        html += `<div class="conflict-panel-header">⚠️ 检测到冲突，请选择处理方式</div>`;

        // Module conflicts
        for (const item of moduleConflicts) {
            html += renderConflictItem(item, 'module');
        }
        // Service conflicts
        for (const item of serviceConflicts) {
            html += renderConflictItem(item, 'service');
        }

        // Quick actions bar
        html += `<div class="conflict-actions-bar">
            <button class="btn-sm" onclick="setAllConflictStrategy('skip')">全部跳过</button>
            <button class="btn-sm" onclick="setAllConflictStrategy('overwrite')">全部覆盖</button>
        </div>`;

        html += `</div>`;
        html += `<button class="btn btn-primary" style="width:100%" onclick="executeRestore()">执行恢复</button>`;
    }

    area.innerHTML = html;
}

function renderConflictItem(item, type) {
    const isModule = type === 'module';
    let detail = '';
    let options = '';

    if (isModule) {
        if (item.conflict_type === 'builtin') {
            detail = `内置模块，将跳过`;
            options = `<select class="conflict-select" data-conflict="${type}" data-name="${escapeHtml(item.name)}" disabled>
                <option value="skip">跳过</option>
            </select>`;
        } else {
            detail = `本地版本 ${item.local_version || '?'}, 备份版本 ${item.backup_version || '?'}`;
            options = `<select class="conflict-select" data-conflict="${type}" data-name="${escapeHtml(item.name)}" onchange="onConflictStrategyChange(this)">
                <option value="skip">跳过</option>
                <option value="overwrite">覆盖</option>
                <option value="rename">重命名</option>
            </select>
            <input type="text" class="conflict-rename-input" data-conflict="${type}" data-name="${escapeHtml(item.name)}-rename" style="display:none" placeholder="新名称" value="${escapeHtml(item.name)}-restored">`;
        }
    } else {
        if (item.conflict_type === 'exists_running') {
            detail = `当前状态: 运行中 🟢`;
            options = `<select class="conflict-select" data-conflict="${type}" data-name="${escapeHtml(item.name)}" onchange="onConflictStrategyChange(this)">
                <option value="skip">跳过</option>
                <option value="stop_and_overwrite">停止并覆盖</option>
                <option value="rename">重命名</option>
            </select>
            <input type="text" class="conflict-rename-input" data-conflict="${type}" data-name="${escapeHtml(item.name)}-rename" style="display:none" placeholder="新名称" value="${escapeHtml(item.name)}-restored">`;
        } else {
            detail = `本地已存在`;
            options = `<select class="conflict-select" data-conflict="${type}" data-name="${escapeHtml(item.name)}" onchange="onConflictStrategyChange(this)">
                <option value="skip">跳过</option>
                <option value="stop_and_overwrite">覆盖</option>
                <option value="rename">重命名</option>
            </select>
            <input type="text" class="conflict-rename-input" data-conflict="${type}" data-name="${escapeHtml(item.name)}-rename" style="display:none" placeholder="新名称" value="${escapeHtml(item.name)}-restored">`;
        }
    }

    return `<div class="conflict-item">
        <div class="conflict-item-info">
            <div class="conflict-item-name">${isModule ? '📦' : '🔧'} ${escapeHtml(item.display_name || item.name)}</div>
            <div class="conflict-item-detail">${detail}</div>
        </div>
        ${options}
    </div>`;
}

function onConflictStrategyChange(selectEl) {
    const name = selectEl.dataset.name;
    const type = selectEl.dataset.conflict;
    const renameInput = document.querySelector(`input[data-name="${name}-rename"]`);
    if (renameInput) {
        renameInput.style.display = selectEl.value === 'rename' ? 'inline-block' : 'none';
    }
}

function setAllConflictStrategy(strategy) {
    document.querySelectorAll('.conflict-select').forEach(el => {
        if (el.disabled) return;
        // For service conflicts, map 'overwrite' to 'stop_and_overwrite'
        if (strategy === 'overwrite' && el.dataset.conflict === 'service') {
            el.value = 'stop_and_overwrite';
        } else {
            el.value = strategy === 'overwrite' ? 'overwrite' : strategy;
        }
        onConflictStrategyChange(el);
    });
}

// ── Restore: Execute ───────────────────────────────
async function executeRestore() {
    if (!backupFile || !previewData || isRestoring) return;
    isRestoring = true;

    // Collect conflict resolution options
    const options = { module_conflicts: {}, service_conflicts: {} };

    document.querySelectorAll('.conflict-select').forEach(el => {
        const name = el.dataset.name;
        const type = el.dataset.conflict;
        const strategy = el.value;

        if (strategy === 'rename') {
            const renameInput = document.querySelector(`input[data-name="${name}-rename"]`);
            const newName = renameInput ? renameInput.value.trim() : '';
            if (!newName) {
                showToast(`请输入 '${name}' 的新名称`, 'warning');
                isRestoring = false;
                return;
            }
            const finalStrategy = `rename:${newName}`;
            if (type === 'module') options.module_conflicts[name] = finalStrategy;
            else options.service_conflicts[name] = finalStrategy;
        } else if (strategy !== 'skip') {
            if (type === 'module') options.module_conflicts[name] = strategy;
            else options.service_conflicts[name] = strategy;
        }
    });

    if (!isRestoring) return;

    // Show restoring state
    const area = document.getElementById('restore-result-area');
    area.innerHTML = `<div class="backup-empty">恢复中，请稍候...</div>`;

    try {
        const result = await api.restoreBackup(backupFile, options);
        renderRestoreResult(result);
    } catch (e) {
        showToast(`恢复失败: ${e.message}`, 'error');
        area.innerHTML = `<div class="backup-empty">恢复失败: ${escapeHtml(e.message)}</div>`;
    } finally {
        isRestoring = false;
    }
}

function renderRestoreResult(result) {
    const area = document.getElementById('restore-result-area');
    let html = '<div class="restore-result">';

    // Restored modules
    if (result.restored_modules.length > 0) {
        html += `<div class="restore-result-section">
            <div class="restore-result-label">已恢复模块</div>
            <div class="restore-result-items">
                ${result.restored_modules.map(n => `<span class="restore-result-tag success">📦 ${escapeHtml(n)}</span>`).join('')}
            </div>
        </div>`;
    }

    // Restored services
    if (result.restored_services.length > 0) {
        html += `<div class="restore-result-section">
            <div class="restore-result-label">已恢复服务</div>
            <div class="restore-result-items">
                ${result.restored_services.map(n => `<span class="restore-result-tag success">🔧 ${escapeHtml(n)}</span>`).join('')}
            </div>
        </div>`;
    }

    // Stopped services
    if (result.stopped_services.length > 0) {
        html += `<div class="restore-result-section">
            <div class="restore-result-label">已停止服务</div>
            <div class="restore-result-items">
                ${result.stopped_services.map(n => `<span class="restore-result-tag stop">⏹ ${escapeHtml(n)}</span>`).join('')}
            </div>
        </div>`;
    }

    // Restored bindings
    if (result.restored_bindings.length > 0) {
        html += `<div class="restore-result-section">
            <div class="restore-result-label">已恢复绑定</div>
            <div class="restore-result-items">
                ${result.restored_bindings.map(b => `<span class="restore-result-tag success">🔗 ${escapeHtml(b.service)} → ${escapeHtml(b.module)}</span>`).join('')}
            </div>
        </div>`;
    }

    // Skipped
    if (result.skipped_modules.length > 0 || result.skipped_services.length > 0) {
        const items = [
            ...result.skipped_modules.map(n => `📦 ${escapeHtml(n)}`),
            ...result.skipped_services.map(n => `🔧 ${escapeHtml(n)}`),
        ];
        html += `<div class="restore-result-section">
            <div class="restore-result-label">已跳过</div>
            <div class="restore-result-items">
                ${items.map(n => `<span class="restore-result-tag skip">${n}</span>`).join('')}
            </div>
        </div>`;
    }

    // Warnings
    if (result.warnings.length > 0) {
        html += `<div class="restore-result-section">
            <div class="restore-result-label">警告</div>
            <div class="restore-result-items">
                ${result.warnings.map(w => `<span class="restore-result-tag warning">⚠ ${escapeHtml(w)}</span>`).join('')}
            </div>
        </div>`;
    }

    // Errors
    if (result.errors.length > 0) {
        html += `<div class="restore-result-section">
            <div class="restore-result-label">错误</div>
            <div class="restore-result-items">
                ${result.errors.map(e => `<span class="restore-result-tag error">✗ ${escapeHtml(e)}</span>`).join('')}
            </div>
        </div>`;
    }

    // Summary
    const total = result.restored_modules.length + result.restored_services.length;
    if (total > 0 && result.errors.length === 0) {
        html += `<div style="margin-top:12px;padding:12px;background:var(--success-muted);border-radius:8px;text-align:center;color:var(--success);font-weight:600">
            恢复完成: ${total} 项成功恢复
        </div>`;
    } else if (result.errors.length > 0) {
        html += `<div style="margin-top:12px;padding:12px;background:var(--danger-muted);border-radius:8px;text-align:center;color:var(--danger);font-weight:600">
            恢复完成，但有 ${result.errors.length} 个错误
        </div>`;
    }

    html += '</div>';
    area.innerHTML = html;
}
