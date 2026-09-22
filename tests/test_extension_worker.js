const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const bgPath = path.join(__dirname, "..", "extension", "background.js");
const bgCode = fs.readFileSync(bgPath, "utf-8");

const allContexts = [];

function setupContext(scriptingImpl, tabsCreatedList = []) {
    const sent = [];
    const wsStub = {
        send: (msg) => sent.push(JSON.parse(msg)),
        readyState: 1,
        close: () => {}
    };

    const listeners = [];
    const context = {
        console: {
            log: () => {},
            error: () => {},
            warn: () => {}
        },
        WebSocket: function() {
            process.nextTick(() => {
                if (typeof wsStub.onopen === "function") {
                    wsStub.onopen();
                }
            });
            return wsStub;
        },
        URL: URL,
        setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 2)),
        clearTimeout: clearTimeout,
        setInterval: (fn, ms) => setInterval(fn, Math.min(ms, 5)),
        clearInterval: clearInterval,
        chrome: {
            storage: {
                local: {
                    get: (defs, cb) => cb(defs)
                },
                onChanged: {
                    addListener: () => {}
                }
            },
            tabs: {
                create: async (opts) => {
                    tabsCreatedList.push(opts);
                    return { id: 101 };
                },
                remove: async () => {},
                get: (id, cb) => cb({ id, status: "complete" }),
                onUpdated: {
                    addListener: (fn) => listeners.push(fn),
                    removeListener: () => {}
                }
            },
            scripting: {
                executeScript: async (opts) => scriptingImpl(opts)
            },
            runtime: {
                lastError: null
            }
        }
    };
    vm.createContext(context);
    vm.runInContext(bgCode, context);
    allContexts.push(context);
    return { context, sent, wsStub };
}

async function runTests() {
    console.log("Starting extension worker tests...");

    // 1. URL routing tests
    // 1.1 Project ID present: navigates to /project/<id>
    {
        const createdTabs = [];
        const { context } = setupContext(async () => {
            return [{ result: { ok: true, token: "tok_test" } }];
        }, createdTabs);
        await context.connectWS();
        await context.handleGetToken({
            req_id: "req_url1",
            action: "IMAGE_GENERATION",
            project_id: "acf65847-adb5-43f2-b7a5-90fcedc816ad"
        });
        assert.strictEqual(createdTabs.length, 1);
        assert.strictEqual(
            createdTabs[0].url,
            "https://flow.google.com/project/acf65847-adb5-43f2-b7a5-90fcedc816ad"
        );
        console.log("✓ URL routing: project_id opens /project/id");
    }

    // 1.2 Project ID missing/empty: fallbacks to homepage
    {
        const createdTabs = [];
        const { context } = setupContext(async () => {
            return [{ result: { ok: true, token: "tok_test" } }];
        }, createdTabs);
        await context.connectWS();
        await context.handleGetToken({
            req_id: "req_url2",
            action: "IMAGE_GENERATION"
        });
        assert.strictEqual(createdTabs.length, 1);
        assert.strictEqual(createdTabs[0].url, "https://flow.google.com/");
        console.log("✓ URL routing: empty project_id fallbacks to https://flow.google.com/");
    }

    // 2. Capture real injected func
    let capturedFunc = null;
    const { context: ctxInit } = setupContext(async (opts) => {
        capturedFunc = opts.func;
        return [{ result: { ok: true, token: "initial" } }];
    });
    await ctxInit.connectWS();
    await ctxInit.handleGetToken({ req_id: "req_init", action: "IMAGE_GENERATION" });
    assert.strictEqual(typeof capturedFunc, "function", "func must be captured from executeScript");

    // Injected func runner helper
    function runInjected(domEnv, action = "IMAGE_GENERATION", timeoutMs = 200) {
        const injectedCtx = {
            grecaptcha: domEnv.grecaptcha,
            document: domEnv.document,
            setTimeout: domEnv.setTimeout || ((fn, ms) => setTimeout(fn, Math.max(1, Math.floor(ms / 20)))),
            clearTimeout: clearTimeout,
            setInterval: domEnv.setInterval || ((fn, ms) => setInterval(fn, Math.max(1, Math.floor(ms / 20)))),
            clearInterval: clearInterval,
            decodeURIComponent: decodeURIComponent,
            String: String,
            Promise: Promise,
            Math: Math
        };
        vm.createContext(injectedCtx);
        const code = "(" + capturedFunc.toString() + ")('" + action + "', " + timeoutMs + ")";
        return vm.runInContext(code, injectedCtx);
    }

    // 2.1 Dynamic render key discovery (excludes explicit, captures actual render key)
    {
        let executedKey = null;
        let executedAction = null;
        const domEnv = {
            document: {
                querySelectorAll: () => [
                    { getAttribute: () => "https://www.google.com/recaptcha/enterprise.js?render=explicit" },
                    { getAttribute: () => "https://www.google.com/recaptcha/enterprise.js?render=DYNAMIC_RENDER_KEY_8888&hl=en" }
                ]
            },
            grecaptcha: {
                enterprise: {
                    ready: (cb) => cb(),
                    execute: async (key, opts) => {
                        executedKey = key;
                        executedAction = opts && opts.action;
                        return "token_dynamic_discovered_123";
                    }
                }
            }
        };
        const res = await runInjected(domEnv, "IMAGE_GENERATION");
        assert.strictEqual(res.ok, true);
        assert.strictEqual(res.token, "token_dynamic_discovered_123");
        assert.strictEqual(executedKey, "DYNAMIC_RENDER_KEY_8888", "Must use discovered key from script[src]");
        assert.strictEqual(executedAction, "IMAGE_GENERATION");
        console.log("✓ Injected func: dynamically discovers sitekey from script render param (skipping explicit)");
    }

    // 2.2 Script missing / not found within ready timeout -> extension_script_load_failed
    {
        const domEnv = {
            document: {
                querySelectorAll: () => [
                    { getAttribute: () => "https://other.domain/bundle.js" }
                ]
            },
            grecaptcha: undefined
        };
        const res = await runInjected(domEnv, "IMAGE_GENERATION", 200);
        assert.strictEqual(res.ok, false);
        assert.strictEqual(res.errorCode, "extension_script_load_failed");
        console.log("✓ Injected func: script not loaded returns extension_script_load_failed");
    }

    // 2.3 异步 sitekey 错误
    {
        const domEnv = {
            document: {
                querySelectorAll: () => [
                    { getAttribute: () => "https://www.google.com/recaptcha/enterprise.js?render=BAD_KEY" }
                ]
            },
            grecaptcha: {
                enterprise: {
                    ready: (cb) => cb(),
                    execute: () => Promise.reject(new Error("Invalid site key: secret_key_val_999"))
                }
            }
        };
        const res = await runInjected(domEnv);
        assert.strictEqual(res.ok, false);
        assert.strictEqual(res.errorCode, "extension_sitekey_invalid");
        assert.strictEqual(JSON.stringify(res).includes("secret_key_val_999"), false);
        console.log("✓ Injected func: async sitekey error classified to extension_sitekey_invalid");
    }

    // 2.4 ready 回调同步 sitekey 错误
    {
        const domEnv = {
            document: {
                querySelectorAll: () => [
                    { getAttribute: () => "https://www.google.com/recaptcha/enterprise.js?render=KEY_1" }
                ]
            },
            grecaptcha: {
                enterprise: {
                    ready: (cb) => cb(),
                    execute: () => {
                        throw new Error("site key is not valid in api.js");
                    }
                }
            }
        };
        const res = await runInjected(domEnv);
        assert.strictEqual(res.ok, false);
        assert.strictEqual(res.errorCode, "extension_sitekey_invalid");
        console.log("✓ Injected func: sync sitekey error in ready classified to extension_sitekey_invalid");
    }

    // 2.5 ready 回调通用同步错误
    {
        const domEnv = {
            document: {
                querySelectorAll: () => [
                    { getAttribute: () => "https://www.google.com/recaptcha/enterprise.js?render=KEY_1" }
                ]
            },
            grecaptcha: {
                enterprise: {
                    ready: (cb) => cb(),
                    execute: () => {
                        throw new Error("Generic execution failure");
                    }
                }
            }
        };
        const res = await runInjected(domEnv);
        assert.strictEqual(res.ok, false);
        assert.strictEqual(res.errorCode, "extension_script_failed");
        console.log("✓ Injected func: sync generic error in ready classified to extension_script_failed");
    }

    // 2.6 CSP / TrustedTypes error
    {
        const domEnv = {
            document: {
                querySelectorAll: () => [
                    { getAttribute: () => "https://www.google.com/recaptcha/enterprise.js?render=KEY_1" }
                ]
            },
            grecaptcha: {
                enterprise: {
                    ready: (cb) => cb(),
                    execute: () => Promise.reject(new Error("Refused to load script due to Content Security Policy / script-src"))
                }
            }
        };
        const res = await runInjected(domEnv);
        assert.strictEqual(res.ok, false);
        assert.strictEqual(res.errorCode, "extension_script_load_failed");
        console.log("✓ Injected func: CSP error classified to extension_script_load_failed");
    }

    // 2.7 Overall scriptTimeoutMs timeout
    {
        const domEnv = {
            document: {
                querySelectorAll: () => [
                    { getAttribute: () => "https://www.google.com/recaptcha/enterprise.js?render=KEY_1" }
                ]
            },
            grecaptcha: {
                enterprise: {
                    ready: () => {} // hangs
                }
            },
            setTimeout: (fn, ms) => setTimeout(fn, Math.max(1, Math.floor(ms / 20)))
        };
        const res = await runInjected(domEnv, "IMAGE_GENERATION", 200);
        assert.strictEqual(res.ok, false);
        assert.strictEqual(res.errorCode, "extension_script_timeout");
        console.log("✓ Injected func: execution timeout classified to extension_script_timeout");
    }

    // 2.8 Empty result from execute
    {
        const domEnv = {
            document: {
                querySelectorAll: () => [
                    { getAttribute: () => "https://www.google.com/recaptcha/enterprise.js?render=KEY_1" }
                ]
            },
            grecaptcha: {
                enterprise: {
                    ready: (cb) => cb(),
                    execute: async () => ""
                }
            }
        };
        const res = await runInjected(domEnv);
        assert.strictEqual(res.ok, false);
        assert.strictEqual(res.errorCode, "extension_empty_result");
        console.log("✓ Injected func: empty token classified to extension_empty_result");
    }

    // 3. handleGetToken WebSocket integration tests
    // 3.1 Token 成功传送到 ws
    {
        const { context, sent } = setupContext(async () => {
            return [{ result: { ok: true, token: "tok_good_999" } }];
        });
        await context.connectWS();
        await context.handleGetToken({ req_id: "req_succ", action: "IMAGE_GENERATION" });
        const msg = sent.find(m => m.req_id === "req_succ");
        assert(msg, "Expected message with req_id req_succ");
        assert.deepStrictEqual(msg, {
            req_id: "req_succ",
            status: "success",
            token: "tok_good_999"
        });
        console.log("✓ handleGetToken: success response delivered with token");
    }

    // 3.2 错误字段必须为 error 而非 errorCode
    {
        const { context, sent } = setupContext(async () => {
            return [{ result: { ok: false, errorCode: "extension_sitekey_invalid" } }];
        });
        await context.connectWS();
        await context.handleGetToken({ req_id: "req_err1", action: "IMAGE_GENERATION" });
        const msg = sent.find(m => m.req_id === "req_err1");
        assert(msg, "Expected message with req_id req_err1");
        assert.strictEqual(msg.status, "error");
        assert.strictEqual(msg.error, "extension_sitekey_invalid");
        assert.strictEqual("errorCode" in msg, false, "Payload must NOT use errorCode field");
        console.log("✓ handleGetToken: error field used, errorCode field absent");
    }

    // 3.3 权限错误 (permission denied)
    {
        const { context, sent } = setupContext(async () => {
            throw new Error("Cannot access contents of url: host permission denied");
        });
        await context.connectWS();
        await context.handleGetToken({ req_id: "req_perm", action: "IMAGE_GENERATION" });
        const msg = sent.find(m => m.req_id === "req_perm");
        assert(msg, "Expected message with req_id req_perm");
        assert.strictEqual(msg.status, "error");
        assert.strictEqual(msg.error, "extension_permission_denied");
        console.log("✓ handleGetToken: access contents / host permission maps to extension_permission_denied");
    }

    // 3.4 普通脚本异常不得误判为 permission (Cannot read properties / No tab)
    {
        const { context, sent } = setupContext(async () => {
            throw new Error("Cannot read properties of undefined (reading 'result')");
        });
        await context.connectWS();
        await context.handleGetToken({ req_id: "req_prop", action: "IMAGE_GENERATION" });
        const msg = sent.find(m => m.req_id === "req_prop");
        assert(msg, "Expected message with req_id req_prop");
        assert.strictEqual(msg.status, "error");
        assert.strictEqual(msg.error, "extension_script_failed");
        console.log("✓ handleGetToken: Cannot read properties maps to extension_script_failed (not permission)");
    }

    // 3.5 executeScript 返回空数组或空结果 (empty results)
    {
        const { context, sent } = setupContext(async () => {
            return [];
        });
        await context.connectWS();
        await context.handleGetToken({ req_id: "req_empty", action: "IMAGE_GENERATION" });
        const msg = sent.find(m => m.req_id === "req_empty");
        assert(msg, "Expected message with req_id req_empty");
        assert.strictEqual(msg.status, "error");
        assert.strictEqual(msg.error, "extension_empty_result");
        console.log("✓ handleGetToken: empty results maps to extension_empty_result");
    }

    // 3.6 ws.onopen 不泄露 api key
    {
        const logs = [];
        const { context } = setupContext(async () => []);
        context.console.log = (...args) => logs.push(args.join(" "));
        context.chrome.storage.local.get = (defs, cb) => {
            cb({
                serverUrl: "ws://127.0.0.1:8000/captcha_ws",
                apiKey: "SUPER_SECRET_KEY_12345",
                routeKey: "test_route",
                clientLabel: "worker_1"
            });
        };
        await context.connectWS();
        await new Promise((r) => setTimeout(r, 10));
        const openLog = logs.find(l => l.includes("Background connected to WebSocket"));
        assert(openLog, "Expected onopen log message");
        assert(!openLog.includes("SUPER_SECRET_KEY_12345"), "Log must not contain apiKey");
        assert(openLog.includes("ws://127.0.0.1:8000/captcha_ws"), "Log must contain origin + pathname");
        console.log("✓ connectWS: ws.onopen logs url.origin + pathname without apiKey");
    }

    console.log("All 16 extension worker checks passed successfully!");
}

runTests()
    .catch(err => {
        console.error("Test failed:", err);
        process.exitCode = 1;
    })
    .finally(() => {
        for (const ctx of allContexts) {
            try {
                if (typeof ctx.closeSocket === "function") {
                    ctx.closeSocket();
                }
            } catch (e) {
                // ignore
            }
        }
    });
