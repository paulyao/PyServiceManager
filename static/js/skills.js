/** Skill 下载页：查看与下载平台开发指南 skill 文件 */

// ── Skill 清单（新增 skill 时把 md 放入 /static/skills/ 并在此登记） ──
const skillList = [
    {
        file: '/static/skills/pyservice-dev.md',
        name: 'pyservice-dev',
        title: 'PyService 自定义模块与自定义服务开发指南',
        description: 'Module 类规范、ctx 对象、生命周期钩子、web-service 注册 API、服务创建全流程与常见坑',
    },
];

// ── Main Render ────────────────────────────────────
function renderSkillsPage() {
    const main = document.getElementById('main-content');
    updateNav('skills');

    main.innerHTML = `
        <div class="page-header">
            <h1>Skill 下载</h1>
            <p style="color:var(--text-muted);margin-top:4px;">PyService 开发指南，可在线查看或下载 Markdown 文件</p>
        </div>
        <div class="skills-list" id="skills-list"></div>
    `;
    renderSkillList();
}

function renderSkillList() {
    const container = document.getElementById('skills-list');
    container.innerHTML = skillList.map((s, i) => `
        <div style="display:flex;align-items:center;justify-content:space-between;background:var(--bg-hover);border:1px solid var(--border);border-radius:8px;padding:16px 20px;margin-bottom:12px;">
            <div>
                <div style="font-weight:600;font-size:15px;">
                    <span style="margin-right:8px">📘</span>${escapeHtml(s.title)}
                    <code style="margin-left:8px;font-size:12px;color:var(--text-muted)">${escapeHtml(s.name)}</code>
                </div>
                <div style="color:var(--text-muted);font-size:13px;margin-top:6px;">${escapeHtml(s.description)}</div>
            </div>
            <div style="display:flex;gap:8px;flex-shrink:0;margin-left:16px;">
                <button class="btn btn-outline btn-sm" onclick="viewSkill(${i})">查看</button>
                <a class="btn btn-primary btn-sm" href="${s.file}" download="${s.name}.md" style="text-decoration:none;display:inline-flex;align-items:center;">下载</a>
            </div>
        </div>
    `).join('');
}

// ── 查看 Skill 内容弹窗 ──────────────────────────────
async function viewSkill(index) {
    const s = skillList[index];
    const overlay = document.getElementById('modal-overlay');
    overlay.innerHTML = `
        <div class="modal" style="max-width:900px;">
            <div class="modal-header">
                <span class="modal-title">${escapeHtml(s.title)}（${escapeHtml(s.name)}.md）</span>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <pre id="skill-content" style="margin:0;padding:16px 20px;max-height:70vh;overflow:auto;font-size:13px;line-height:1.6;white-space:pre-wrap;word-break:break-word;background:var(--bg-hover);border-radius:0 0 8px 8px;color:var(--text);">加载中...</pre>
            <div class="modal-footer">
                <a class="btn btn-primary" href="${s.file}" download="${s.name}.md" style="text-decoration:none;">下载 .md 文件</a>
                <button class="btn btn-outline" onclick="closeModal()">关闭</button>
            </div>
        </div>
    `;
    overlay.classList.add('open');
    try {
        const resp = await fetch(s.file);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const text = await resp.text();
        document.getElementById('skill-content').textContent = text;
    } catch (e) {
        document.getElementById('skill-content').textContent = `加载失败: ${e.message}`;
    }
}
