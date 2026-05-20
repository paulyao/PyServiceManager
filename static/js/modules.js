/** 模块管理页面 */
async function renderModulesPage() {
    const main = document.getElementById('main-content');
    updateNav('modules');

    try {
        const modules = await api.listModules();
        main.innerHTML = `
            <div class="page-header">
                <div>
                    <div class="page-title">模块管理</div>
                    <div class="page-subtitle">${modules.length} 个可用模块</div>
                </div>
                <button class="btn btn-primary" onclick="openCreateModuleModal()">+ 新建模块</button>
            </div>
            <div class="card-grid" id="module-cards">
                ${modules.length === 0 ? `
                    <div class="empty-state" style="grid-column:1/-1">
                        <div class="icon">📦</div>
                        <div class="title">暂无模块</div>
                        <p>创建模块来扩展你的服务功能</p>
                    </div>
                ` : modules.map(m => renderModuleCard(m)).join('')}
            </div>
        `;
    } catch (e) {
        main.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><div class="title">出错了</div><p>${escapeHtml(e.message)}</p></div>`;
    }
}

function renderModuleCard(m) {
    return `
        <div class="card" data-module="${m.name}">
            <div class="module-card-header">
                <span class="module-name">${escapeHtml(m.display_name)}</span>
                <span class="module-version">v${escapeHtml(m.version)}</span>
            </div>
            <div class="module-desc">${escapeHtml(m.description || '暂无描述')}</div>
            <div class="module-meta">
                <span>🔗 ${m.service_count} 个服务</span>
                <span>${m.code_source === 'editor' ? '在线编辑' : m.code_source === 'upload' ? '上传文件' : m.code_source}</span>
            </div>
            <div style="margin-top:12px;display:flex;gap:8px">
                <button class="btn btn-outline btn-sm" onclick="openEditModuleModal('${m.name}')">编辑代码</button>
                <button class="btn btn-outline btn-sm" onclick="viewModuleServices('${m.name}')">关联服务</button>
                <button class="btn btn-outline btn-sm btn-icon" onclick="deleteModule('${m.name}')" title="删除">🗑️</button>
            </div>
        </div>
    `;
}

// ── 创建模块弹窗 ──────────────────────────────────────
function openCreateModuleModal() {
    const overlay = document.getElementById('modal-overlay');
    overlay.innerHTML = `
        <div class="modal">
            <div class="modal-header">
                <span class="modal-title">新建模块</span>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label">模块名称 *</label>
                <input class="form-input" id="mod-name" placeholder="my-module" pattern="[a-zA-Z][a-zA-Z0-9_-]{0,63}">
            </div>
            <div class="form-group">
                <label class="form-label">显示名称 *</label>
                <input class="form-input" id="mod-display" placeholder="我的模块">
            </div>
            <div class="form-group">
                <label class="form-label">描述</label>
                <textarea class="form-textarea" id="mod-desc" placeholder="该模块的功能描述"></textarea>
            </div>
            <div class="form-group">
                <label class="form-label">版本</label>
                <input class="form-input" id="mod-version" value="1.0.0">
            </div>
            <div class="form-group">
                <label class="form-label">代码来源</label>
                <div style="display:flex;gap:16px;margin-top:6px">
                    <label><input type="radio" name="mod-code-source" value="editor" checked> 在线编辑</label>
                    <label><input type="radio" name="mod-code-source" value="upload"> 上传文件</label>
                </div>
            </div>
            <div id="mod-editor-section">
                <div class="form-group">
                    <label class="form-label">模块代码</label>
                    <textarea class="form-textarea" id="mod-code" rows="18" style="font-family:monospace;font-size:13px">${escapeHtml(getDefaultModuleCode())}</textarea>
                </div>
                <div id="mod-validation-result"></div>
            </div>
            <div id="mod-upload-section" style="display:none">
                <div class="form-group">
                    <label class="form-label">上传模块文件 (.py)</label>
                    <input type="file" id="mod-upload" accept=".py" class="form-input">
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">模块配置 (TOML，可选)</label>
                <textarea class="form-textarea" id="mod-config" rows="5" style="font-family:monospace;font-size:13px" placeholder="[settings]\nkey = \"value\""></textarea>
            </div>
            <div class="modal-footer">
                <button class="btn btn-outline" onclick="closeModal()">取消</button>
                <button class="btn btn-outline btn-sm" onclick="validateModuleCode()">验证</button>
                <button class="btn btn-primary" onclick="createModule()">创建</button>
            </div>
        </div>
    `;
    overlay.classList.add('open');

    document.querySelectorAll('input[name="mod-code-source"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            document.getElementById('mod-editor-section').style.display = e.target.value === 'editor' ? 'block' : 'none';
            document.getElementById('mod-upload-section').style.display = e.target.value === 'upload' ? 'block' : 'none';
        });
    });
}

function getDefaultModuleCode() {
    return `from pathlib import Path


class Module:
    """模块元数据"""
    name = "my-module"
    version = "1.0.0"
    description = "模块描述"

    # 生命周期钩子（可选）
    def on_start(self, ctx):
        """服务启动时调用。"""
        ctx.logger.info(f"模块 {self.name} 已启动")

    def on_stop(self, ctx):
        """服务停止时调用。"""
        ctx.logger.info(f"模块 {self.name} 已停止")

    def on_config_reload(self, ctx):
        """配置热加载时调用。"""
        pass

    # 工具方法（在用户代码中通过 modules["module-name"] 访问）
    def my_utility(self, *args, **kwargs):
        """供用户代码调用的工具函数。"""
        pass
`;
}

async function validateModuleCode() {
    const code = document.getElementById('mod-code')?.value;
    if (!code) return;

    try {
        const result = await api.validateModule(code);
        const panel = document.getElementById('mod-validation-result');
        if (!panel) return;

        if (result.valid) {
            const info = result.module_info || {};
            panel.innerHTML = `
                <div class="validation-panel valid">
                    <h4 style="color:var(--success)">✓ 模块验证通过</h4>
                    <div style="margin-top:8px">
                        <strong>名称:</strong> ${escapeHtml(info.name || '-')} &nbsp;
                        <strong>版本:</strong> ${escapeHtml(info.version || '-')}
                    </div>
                    <div style="margin-top:6px">
                        <strong>生命周期钩子:</strong>
                        ${info.has_on_start ? '<span class="hook-tag">on_start</span>' : ''}
                        ${info.has_on_stop ? '<span class="hook-tag">on_stop</span>' : ''}
                        ${info.has_on_config_reload ? '<span class="hook-tag">on_config_reload</span>' : ''}
                        ${info.has_on_error ? '<span class="hook-tag">on_error</span>' : ''}
                        ${!info.has_on_start && !info.has_on_stop && !info.has_on_config_reload && !info.has_on_error ? '<span style="color:var(--text-muted)">无</span>' : ''}
                    </div>
                    <div style="margin-top:6px">
                        <strong>公共方法:</strong>
                        ${(info.public_methods || []).map(m => `<span class="method-tag">${escapeHtml(m)}</span>`).join('') || '<span style="color:var(--text-muted)">无</span>'}
                    </div>
                </div>
            `;
        } else {
            panel.innerHTML = `
                <div class="validation-panel invalid">
                    <h4 style="color:var(--danger)">✗ 模块验证失败</h4>
                    <ul style="margin-top:4px;color:var(--danger)">${result.errors.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>
                </div>
            `;
        }
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function createModule() {
    const name = document.getElementById('mod-name').value.trim();
    const displayName = document.getElementById('mod-display').value.trim();
    if (!name) { showToast('请输入模块名称', 'error'); return; }
    if (!displayName) { showToast('请输入显示名称', 'error'); return; }

    const codeSource = document.querySelector('input[name="mod-code-source"]:checked').value;
    let code = null;

    if (codeSource === 'editor') {
        code = document.getElementById('mod-code').value;
    }

    const configToml = document.getElementById('mod-config').value.trim() || null;

    try {
        await api.createModule({
            name,
            display_name: displayName,
            description: document.getElementById('mod-desc').value.trim(),
            version: document.getElementById('mod-version').value.trim() || '1.0.0',
            code_source: codeSource,
            code,
            config_toml: configToml,
        });

        if (codeSource === 'upload') {
            const fileInput = document.getElementById('mod-upload');
            if (fileInput.files[0]) {
                await api.uploadModuleFile(name, fileInput.files[0]);
            }
        }

        closeModal();
        showToast(`模块 '${name}' 已创建`);
        renderModulesPage();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// ── 编辑模块弹窗 ────────────────────────────────────────
async function openEditModuleModal(name) {
    try {
        const data = await api.getModuleCode(name);
        const overlay = document.getElementById('modal-overlay');
        overlay.innerHTML = `
            <div class="modal" style="max-width:900px">
                <div class="modal-header">
                    <span class="modal-title">编辑模块: ${escapeHtml(name)}</span>
                    <button class="modal-close" onclick="closeModal()">&times;</button>
                </div>
                <div style="display:flex;gap:16px">
                    <div style="flex:1">
                        <div class="form-group">
                            <label class="form-label">module.py</label>
                            <textarea id="edit-mod-code" class="form-textarea" rows="20" style="font-family:monospace;font-size:13px">${escapeHtml(data.code)}</textarea>
                        </div>
                    </div>
                    <div style="width:280px">
                        <div class="form-group">
                            <label class="form-label">config.toml</label>
                            <textarea id="edit-mod-config" class="form-textarea" rows="8" style="font-family:monospace;font-size:13px">${escapeHtml(data.config_toml || '')}</textarea>
                        </div>
                        <div id="edit-mod-validation"></div>
                        <div class="form-group" style="margin-top:12px">
                            <label class="form-label">上传替换文件</label>
                            <input type="file" accept=".py" class="form-input" onchange="loadModuleFileToEditor(this)">
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-outline btn-sm" onclick="validateEditModuleCode()">验证</button>
                    <button class="btn btn-outline" onclick="closeModal()">取消</button>
                    <button class="btn btn-primary" onclick="saveModuleCode('${name}')">保存</button>
                </div>
            </div>
        `;
        overlay.classList.add('open');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function loadModuleFileToEditor(input) {
    if (input.files[0]) {
        const reader = new FileReader();
        reader.onload = (e) => {
            const editor = document.getElementById('edit-mod-code');
            if (editor) editor.value = e.target.result;
        };
        reader.readAsText(input.files[0]);
    }
}

async function validateEditModuleCode() {
    const code = document.getElementById('edit-mod-code')?.value;
    if (!code) return;

    try {
        const result = await api.validateModule(code);
        const panel = document.getElementById('edit-mod-validation');
        if (!panel) return;

        if (result.valid) {
            const info = result.module_info || {};
            panel.innerHTML = `
                <div class="validation-panel valid">
                    <h4 style="color:var(--success);font-size:12px">✓ 验证通过</h4>
                    <div style="margin-top:4px;font-size:12px">
                        钩子: ${[info.has_on_start ? 'on_start' : '', info.has_on_stop ? 'on_stop' : '', info.has_on_config_reload ? 'on_reload' : ''].filter(Boolean).map(h => `<span class="hook-tag">${h}</span>`).join('') || '无'}
                    </div>
                    <div style="margin-top:4px;font-size:12px">
                        方法: ${(info.public_methods || []).map(m => `<span class="method-tag">${escapeHtml(m)}</span>`).join('') || '无'}
                    </div>
                </div>
            `;
        } else {
            panel.innerHTML = `
                <div class="validation-panel invalid">
                    <h4 style="color:var(--danger);font-size:12px">✗ 验证失败</h4>
                    <ul style="font-size:12px;margin-top:2px;color:var(--danger)">${result.errors.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>
                </div>
            `;
        }
    } catch (e) { showToast(e.message, 'error'); }
}

async function saveModuleCode(name) {
    const code = document.getElementById('edit-mod-code').value;
    const configToml = document.getElementById('edit-mod-config').value.trim() || null;

    try {
        const result = await api.updateModuleCode(name, code, configToml);
        showToast('模块代码已保存' + (result.restart_required ? ` - ${result.affected_services} 个服务需要重启` : ''));
        closeModal();
        renderModulesPage();
    } catch (e) { showToast(e.message, 'error'); }
}

// ── 模块操作 ───────────────────────────────────────────
async function deleteModule(name) {
    if (!confirm(`确定要删除模块 '${name}' 吗？此操作将解除与所有服务的绑定。`)) return;
    try {
        await api.deleteModule(name);
        showToast(`模块 '${name}' 已删除`);
        renderModulesPage();
    } catch (e) { showToast(e.message, 'error'); }
}

async function viewModuleServices(name) {
    try {
        const data = await api.getModuleServices(name);
        const services = data.services || [];
        if (services.length === 0) {
            showToast(`模块 '${name}' 未被任何服务使用`);
        } else {
            const list = services.map(s => `${s.name} (${s.status === 'running' ? '运行中' : s.status === 'stopped' ? '已停止' : s.status})`).join(', ');
            showToast(`被以下服务使用: ${list}`, 'warning');
        }
    } catch (e) { showToast(e.message, 'error'); }
}
