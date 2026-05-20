/** CodeMirror 6 编辑器封装模块 (ES Module) */
import { basicSetup } from "codemirror";
import { EditorView } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { python } from "@codemirror/lang-python";
import { oneDark } from "@codemirror/theme-one-dark";
import { LRLanguage, syntaxHighlighting, HighlightStyle } from "@codemirror/language";
import { tags } from "@lezer/highlight";
import { parser } from "@codemovie/lezer-toml";

// ── 实例注册表 ─────────────────────────────────────
const cmInstances = new Map();

// ── TOML 语言支持 ──────────────────────────────────
const tomlLanguage = LRLanguage.define({
    parser: parser,
    languageData: {
        commentTokens: { line: "#" },
        indentOnInput: /^\s*[}\]]$/,
    }
});

const tomlHighlighting = syntaxHighlighting(HighlightStyle.define([
    { tag: tags.comment, color: "#546e7a", fontStyle: "italic" },
    { tag: tags.string, color: "#c3e88d" },
    { tag: tags.number, color: "#f78c6c" },
    { tag: tags.bool, color: "#ffcb6b" },
    { tag: tags.className, color: "#82aaff" },       // TOML table headers
    { tag: tags.propertyName, color: "#c792ea" },     // TOML keys
    { tag: tags.null, color: "#ffcb6b" },             // TOML bare values
    { tag: tags.atom, color: "#ffcb6b" },             // TOML dates
]));

function toml() {
    return [tomlLanguage, tomlHighlighting];
}

// ── 暗色主题 ────────────────────────────────────────
const pyserviceDarkTheme = EditorView.theme({
    "&": {
        backgroundColor: "#1e2130",
        color: "#e1e4ed",
    },
    ".cm-content": {
        caretColor: "#6c5ce7",
    },
    ".cm-cursor, .cm-dropCursor": {
        borderLeftColor: "#6c5ce7",
    },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection": {
        backgroundColor: "rgba(108, 92, 231, 0.25)",
    },
    ".cm-panels": {
        backgroundColor: "#1a1d27",
        color: "#e1e4ed",
    },
    ".cm-panels.cm-panels-top": {
        borderBottom: "1px solid #2d3148",
    },
    ".cm-searchMatch": {
        backgroundColor: "rgba(108, 92, 231, 0.3)",
        outline: "1px solid rgba(108, 92, 231, 0.5)",
    },
    ".cm-searchMatch.cm-searchMatch-selected": {
        backgroundColor: "rgba(108, 92, 231, 0.45)",
    },
    ".cm-activeLine": {
        backgroundColor: "rgba(255, 255, 255, 0.03)",
    },
    ".cm-selectionMatch": {
        backgroundColor: "rgba(108, 92, 231, 0.2)",
    },
    ".cm-matchingBracket, .cm-nonmatchingBracket": {
        backgroundColor: "rgba(108, 92, 231, 0.3)",
        outline: "1px solid #6c5ce7",
    },
    ".cm-gutters": {
        backgroundColor: "#181b28",
        color: "#5c6078",
        border: "none",
        borderRight: "1px solid #2d3148",
    },
    ".cm-activeLineGutter": {
        backgroundColor: "rgba(255, 255, 255, 0.03)",
        color: "#8b8fa3",
    },
    ".cm-foldPlaceholder": {
        backgroundColor: "transparent",
        border: "none",
        color: "#5c6078",
    },
    ".cm-tooltip": {
        backgroundColor: "#1a1d27",
        border: "1px solid #2d3148",
        color: "#e1e4ed",
    },
    ".cm-tooltip .cm-tooltip-arrow:before": {
        borderTopColor: "#2d3148",
        borderBottomColor: "#2d3148",
    },
    ".cm-tooltip .cm-tooltip-arrow:after": {
        borderTopColor: "#1a1d27",
        borderBottomColor: "#1a1d27",
    },
    ".cm-tooltip-autocomplete": {
        "& > ul > li": {
            padding: "2px 6px",
        },
        "& > ul > li[aria-selected]": {
            backgroundColor: "rgba(108, 92, 231, 0.3)",
            color: "#e1e4ed",
        },
    },
}, { dark: true });

const pyserviceDarkHighlightStyle = HighlightStyle.define([
    { tag: tags.keyword, color: "#c792ea" },
    { tag: tags.operator, color: "#89ddff" },
    { tag: tags.definition(tags.variableName), color: "#82aaff" },
    { tag: tags.variableName, color: "#e1e4ed" },
    { tag: tags.function(tags.variableName), color: "#82aaff" },
    { tag: tags.definition(tags.propertyName), color: "#c792ea" },
    { tag: tags.propertyName, color: "#c792ea" },
    { tag: tags.string, color: "#c3e88d" },
    { tag: tags.number, color: "#f78c6c" },
    { tag: tags.bool, color: "#ffcb6b" },
    { tag: tags.null, color: "#ffcb6b" },
    { tag: tags.comment, color: "#546e7a", fontStyle: "italic" },
    { tag: tags.className, color: "#ffcb6b" },
    { tag: tags.definition(tags.className), color: "#ffcb6b" },
    { tag: tags.typeName, color: "#ffcb6b" },
    { tag: tags.labelName, color: "#82aaff" },
    { tag: tags.separator, color: "#89ddff" },
    { tag: tags.punctuation, color: "#89ddff" },
    { tag: tags.bracket, color: "#89ddff" },
    { tag: tags.atom, color: "#f78c6c" },
    { tag: tags.meta, color: "#ffcb6b" },
    { tag: tags.inserted, color: "#c3e88d" },
    { tag: tags.deleted, color: "#f07178" },
    { tag: tags.changed, color: "#ffcb6b" },
]);

const pyserviceHighlightExtension = syntaxHighlighting(pyserviceDarkHighlightStyle);

// ── 高度控制 ────────────────────────────────────────
function heightExtension(height) {
    if (!height) return [];
    return EditorView.theme({
        ".cm-scroller": {
            maxHeight: height,
            overflowY: "auto",
        }
    });
}

// ── 共享扩展 ────────────────────────────────────────
function sharedExtensions(height) {
    return [
        pyserviceDarkTheme,
        pyserviceHighlightExtension,
        heightExtension(height),
    ];
}

// ── 创建编辑器 ──────────────────────────────────────
function createEditor(containerId, languageExtensions, options = {}) {
    // 如果已有同 ID 实例，先销毁
    if (cmInstances.has(containerId)) {
        const existing = cmInstances.get(containerId);
        existing.destroy();
        cmInstances.delete(containerId);
    }

    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`Editor container #${containerId} not found`);
        return null;
    }

    const extensions = [
        basicSetup,
        ...languageExtensions,
        ...sharedExtensions(options.height),
    ];

    if (options.readonly) {
        extensions.push(EditorState.readOnly.of(true));
    }

    const state = EditorState.create({
        doc: options.initialValue || '',
        extensions,
    });

    const view = new EditorView({
        state,
        parent: container,
    });

    cmInstances.set(containerId, view);
    return view;
}

function createPythonEditor(containerId, options = {}) {
    return createEditor(containerId, [
        python(),
        EditorState.tabSize.of(4),
    ], options);
}

function createTomlEditor(containerId, options = {}) {
    return createEditor(containerId, [
        toml(),
        EditorState.tabSize.of(2),
    ], options);
}

// ── 值读写 ──────────────────────────────────────────
function getEditorValue(containerId) {
    const view = cmInstances.get(containerId);
    if (!view) return '';
    return view.state.doc.toString();
}

function setEditorValue(containerId, value) {
    const view = cmInstances.get(containerId);
    if (!view) return;
    view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: value }
    });
}

// ── 销毁 ────────────────────────────────────────────
function destroyEditor(containerId) {
    const view = cmInstances.get(containerId);
    if (view) {
        view.destroy();
        cmInstances.delete(containerId);
    }
}

function destroyAllEditors() {
    for (const [id, view] of cmInstances) {
        view.destroy();
    }
    cmInstances.clear();
}

// ── 注册到全局 ──────────────────────────────────────
const api = {
    createPythonEditor,
    createTomlEditor,
    getEditorValue,
    setEditorValue,
    destroyEditor,
    destroyAllEditors,
};

if (window.CMReadyResolve) {
    window.CMReadyResolve(api);
}

export { createPythonEditor, createTomlEditor, getEditorValue, setEditorValue, destroyEditor, destroyAllEditors };
