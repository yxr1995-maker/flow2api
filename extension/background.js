let ws = null;
let reconnectTimeout = null;
let heartbeatInterval = null;

const DEFAULT_SETTINGS = {
    serverUrl: "ws://127.0.0.1:8000/captcha_ws",
    apiKey: "",
    routeKey: "",
    clientLabel: ""
};

function getSettings() {
    return new Promise((resolve) => {
        chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
            resolve({
                serverUrl: (stored.serverUrl || DEFAULT_SETTINGS.serverUrl).trim(),
                apiKey: (stored.apiKey || "").trim(),
                routeKey: (stored.routeKey || "").trim(),
                clientLabel: (stored.clientLabel || "").trim()
            });
        });
    });
}

function closeSocket() {
    if (heartbeatInterval) clearInterval(heartbeatInterval);
    heartbeatInterval = null;
    if (reconnectTimeout) clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
    if (ws) {
        try {
            ws.close();
        } catch (e) {
            console.log("[Flow2API] Close socket error", e);
        }
        ws = null;
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function waitForTabReady(tabId, timeoutMs = 12000) {
    return new Promise((resolve) => {
        let settled = false;
        const finish = () => {
            if (settled) return;
            settled = true;
            chrome.tabs.onUpdated.removeListener(onUpdated);
            clearTimeout(timer);
            resolve();
        };
        const onUpdated = (updatedTabId, changeInfo) => {
            if (updatedTabId === tabId && changeInfo.status === "complete") {
                finish();
            }
        };
        const timer = setTimeout(finish, timeoutMs);

        chrome.tabs.onUpdated.addListener(onUpdated);
        chrome.tabs.get(tabId, (tab) => {
            if (chrome.runtime.lastError) {
                finish();
                return;
            }
            if (tab && tab.status === "complete") {
                finish();
            }
        });
    });
}

async function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

    const settings = await getSettings();
    const url = new URL(settings.serverUrl || DEFAULT_SETTINGS.serverUrl);
    if (settings.apiKey) {
        url.searchParams.set("key", settings.apiKey);
    }
    if (settings.routeKey) {
        url.searchParams.set("route_key", settings.routeKey);
    }
    if (settings.clientLabel) {
        url.searchParams.set("client_label", settings.clientLabel);
    }

    ws = new WebSocket(url.toString());

    ws.onopen = () => {
        console.log("[Flow2API] Background connected to WebSocket", url.origin + url.pathname);
        ws.send(JSON.stringify({
            type: "register",
            route_key: settings.routeKey,
            client_label: settings.clientLabel
        }));
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        heartbeatInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "ping" }));
            }
        }, 20000);
    };

    let tokenQueue = Promise.resolve();

    ws.onmessage = async (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            return;
        }

        if (data.type === "register_ack") {
            console.log("[Flow2API] Registered route key:", data.route_key || "(empty)");
            return;
        }

        if (data.type === "get_token") {
            tokenQueue = tokenQueue.then(() => handleGetToken(data)).catch(err => {
                console.error("[Flow2API] Queue Error:", err);
            });
        }
    };

    ws.onclose = () => {
        console.log("[Flow2API] WebSocket Closed. Reconnecting in 2s...");
        ws = null;
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        if (reconnectTimeout) clearTimeout(reconnectTimeout);
        reconnectTimeout = setTimeout(connectWS, 2000);
    };

    ws.onerror = (e) => {
        console.log("[Flow2API] WebSocket Error", e);
    };
}

async function handleGetToken(data) {
    let newTabId = null;
    const sendError = (errorCode) => {
        ws.send(JSON.stringify({
            req_id: data.req_id,
            status: "error",
            error: errorCode
        }));
    };
    try {
        const projectId = (data && data.project_id ? String(data.project_id).trim() : "");
        const targetUrl = projectId
            ? `https://flow.google.com/project/${encodeURIComponent(projectId)}`
            : "https://flow.google.com/";
        console.log("[Flow2API] Opening Flow page:", targetUrl);
        const newTab = await chrome.tabs.create({ url: targetUrl, active: false });
        newTabId = newTab.id;

        await waitForTabReady(newTabId);
        await sleep(1200);

        let successResponse = null;
        let lastErrorCode = "extension_script_failed";
        const scriptTimeoutMs = data.action === "VIDEO_GENERATION" ? 30000 : 20000;

        try {
            const results = await chrome.scripting.executeScript({
                target: { tabId: newTabId },
                world: "MAIN",
                func: async (action, scriptTimeoutMs) => {
                    return new Promise((resolve) => {
                        let settled = false;
                        let pollInterval = null;
                        let readyTimeout = null;
                        let overallTimer = null;

                        const finish = (value) => {
                            if (settled) return;
                            settled = true;
                            if (overallTimer) clearTimeout(overallTimer);
                            if (readyTimeout) clearTimeout(readyTimeout);
                            if (pollInterval) clearInterval(pollInterval);
                            overallTimer = null;
                            readyTimeout = null;
                            pollInterval = null;
                            resolve(value);
                        };

                        function classify(e) {
                            const msg = (e && (e.message || e.toString())) ? String(e.message || e) : "";
                            if (/Invalid site key|Invalid key type|not loaded in api.js|site key is not valid/i.test(msg)) {
                                return "extension_sitekey_invalid";
                            }
                            if (/TrustedScriptURL|Trusted Types|Content Security Policy|CSP|script-src/i.test(msg)) {
                                return "extension_script_load_failed";
                            }
                            return "extension_script_failed";
                        }

                        function findSiteKey() {
                            const scripts = document.querySelectorAll ? document.querySelectorAll("script[src]") : (document.scripts || []);
                            for (let i = 0; i < scripts.length; i++) {
                                const s = scripts[i];
                                const src = (s.getAttribute ? s.getAttribute("src") : s.src) || "";
                                if (src.indexOf("recaptcha/enterprise.js") === -1) continue;
                                const m = src.match(/[?&]render=([^&]+)/);
                                if (m && m[1]) {
                                    const key = decodeURIComponent(m[1]).trim();
                                    if (key && key.toLowerCase() !== "explicit") {
                                        return key;
                                    }
                                }
                            }
                            return null;
                        }

                        try {
                            overallTimer = setTimeout(() => {
                                finish({ ok: false, errorCode: "extension_script_timeout" });
                            }, scriptTimeoutMs);

                            const maxReadyWaitMs = Math.min(10000, Math.floor(scriptTimeoutMs / 2));
                            readyTimeout = setTimeout(() => {
                                if (pollInterval) clearInterval(pollInterval);
                                pollInterval = null;
                                finish({ ok: false, errorCode: "extension_script_load_failed" });
                            }, maxReadyWaitMs);

                            function run(siteKey) {
                                try {
                                    grecaptcha.enterprise.ready(function() {
                                        try {
                                            grecaptcha.enterprise.execute(siteKey, { action: action })
                                                .then(token => {
                                                    if (token) {
                                                        finish({ ok: true, token: token });
                                                    } else {
                                                        finish({ ok: false, errorCode: "extension_empty_result" });
                                                    }
                                                })
                                                .catch(err => finish({ ok: false, errorCode: classify(err) }));
                                        } catch (e) {
                                            finish({ ok: false, errorCode: classify(e) });
                                        }
                                    });
                                } catch (e) {
                                    finish({ ok: false, errorCode: classify(e) });
                                }
                            }

                            function checkReady() {
                                try {
                                    const key = findSiteKey();
                                    const hasEnterprise = typeof grecaptcha !== "undefined" && grecaptcha && grecaptcha.enterprise;
                                    if (key && hasEnterprise) {
                                        if (readyTimeout) clearTimeout(readyTimeout);
                                        if (pollInterval) clearInterval(pollInterval);
                                        readyTimeout = null;
                                        pollInterval = null;
                                        run(key);
                                        return true;
                                    }
                                } catch (e) {
                                    // continue polling until readyTimeout
                                }
                                return false;
                            }

                            if (!checkReady()) {
                                pollInterval = setInterval(checkReady, 100);
                            }
                        } catch (e) {
                            finish({ ok: false, errorCode: classify(e) });
                        }
                    });
                },
                args: [data.action || "IMAGE_GENERATION", scriptTimeoutMs]
            });

            if (!results || !results.length || !results[0] || results[0].result === undefined || results[0].result === null) {
                lastErrorCode = "extension_empty_result";
            } else {
                const result = results[0].result;
                if (result && result.ok === true && typeof result.token === "string" && result.token) {
                    successResponse = { status: "success", token: result.token };
                } else if (result && typeof result.errorCode === "string" && result.errorCode) {
                    lastErrorCode = result.errorCode;
                } else {
                    lastErrorCode = "extension_empty_result";
                }
            }
        } catch (e) {
            const msg = (e && e.message) ? String(e.message) : "";
            if (/access contents|host permission/i.test(msg)) {
                lastErrorCode = "extension_permission_denied";
            } else {
                lastErrorCode = "extension_script_failed";
            }
        }

        if (successResponse) {
            ws.send(JSON.stringify({
                req_id: data.req_id,
                status: successResponse.status,
                token: successResponse.token
            }));
        } else {
            sendError(lastErrorCode);
        }
    } catch (err) {
        sendError("extension_script_failed");
    } finally {
        if (newTabId) {
            try {
                await chrome.tabs.remove(newTabId);
                console.log("[Flow2API] Closed temporary token tab.");
            } catch (e) {
                console.log("[Flow2API] Error closing tab:", e);
            }
        }
    }
}

chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.routeKey || changes.serverUrl || changes.apiKey || changes.clientLabel) {
        console.log("[Flow2API] Extension settings changed, reconnecting WebSocket...");
        closeSocket();
        connectWS();
    }
});

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        handleGetToken,
        connectWS,
        closeSocket,
        getSettings,
        DEFAULT_SETTINGS,
        waitForTabReady,
        sleep
    };
}

if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id) {
    connectWS();
}
