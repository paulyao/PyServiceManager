/** API utility module */
const API_BASE = '/api/v1';

async function request(method, path, body = null) {
    const opts = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (body && !(body instanceof FormData)) {
        opts.body = JSON.stringify(body);
    } else if (body instanceof FormData) {
        delete opts.headers['Content-Type'];
        opts.body = body;
    }
    const res = await fetch(`${API_BASE}${path}`, opts);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        let errorMsg = err.detail;
        if (typeof errorMsg === 'object') {
            errorMsg = errorMsg.error || errorMsg.detail || JSON.stringify(errorMsg);
        }
        throw new Error(errorMsg || err.error || res.statusText);
    }
    if (res.status === 204) return null;
    return res.json();
}

const api = {
    // Services
    listServices: () => request('GET', '/services'),
    getService: (name) => request('GET', `/services/${name}`),
    createService: (data) => request('POST', '/services', data),
    deleteService: (name) => request('DELETE', `/services/${name}`),
    startService: (name) => request('POST', `/services/${name}/start`),
    stopService: (name) => request('POST', `/services/${name}/stop`),
    restartService: (name) => request('POST', `/services/${name}/restart`),
    enableService: (name) => request('POST', `/services/${name}/enable`),
    disableService: (name) => request('POST', `/services/${name}/disable`),
    getServiceStatus: (name) => request('GET', `/services/${name}/status`),

    // Service code
    getServiceCode: (name) => request('GET', `/services/${name}/code`),
    updateServiceCode: (name, code) => request('PUT', `/services/${name}/code`, { code }),
    uploadServiceScript: (name, file) => {
        const fd = new FormData();
        fd.append('file', file);
        return request('POST', `/services/${name}/upload`, fd);
    },

    // Service config
    getServiceConfig: (name) => request('GET', `/services/${name}/config`),
    updateServiceConfig: (name, config) => request('PUT', `/services/${name}/config`, { config }),

    // Service logs
    getServiceLogs: (name, lines = 200) => request('GET', `/services/${name}/logs?lines=${lines}`),
    getLogWsUrl: (name) => {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${proto}//${location.host}${API_BASE}/services/${name}/logs/ws`;
    },

    // Service modules
    getServiceModules: (name) => request('GET', `/modules/service/${name}`),
    updateServiceModules: (name, modules) => request('PUT', `/modules/service/${name}`, { modules }),

    // Modules
    listModules: () => request('GET', '/modules'),
    getModule: (name) => request('GET', `/modules/${name}`),
    createModule: (data) => request('POST', '/modules', data),
    updateModule: (name, data) => request('PUT', `/modules/${name}`, data),
    deleteModule: (name) => request('DELETE', `/modules/${name}`),

    // Module code
    getModuleCode: (name) => request('GET', `/modules/${name}/code`),
    updateModuleCode: (name, code, config_toml) => request('PUT', `/modules/${name}/code`, { code, config_toml }),
    uploadModuleFile: (name, file) => {
        const fd = new FormData();
        fd.append('file', file);
        return request('POST', `/modules/${name}/upload`, fd);
    },
    validateModule: (code) => request('POST', '/modules/validate', { code }),

    // Module services
    getModuleServices: (name) => request('GET', `/modules/${name}/services`),

    // Module dependencies
    getModuleDeps: (name) => request('GET', `/modules/${name}/deps`),
    installModuleDeps: (name) => request('POST', `/modules/${name}/deps/install`),
    updateModuleRequirements: (name, requirements) => request('PUT', `/modules/${name}/requirements`, { requirements }),

    // Service dependencies
    getServiceDeps: (name) => request('GET', `/services/${name}/deps`),
    installServiceDeps: (name) => request('POST', `/services/${name}/deps/install`),
    updateServiceRequirements: (name, requirements) => request('PUT', `/services/${name}/requirements`, { requirements }),

    // Health
    health: () => request('GET', '/health'),
};
