/**
 * Turnstile VM executor — runs the actual SDK code with mocked browser globals
 * Called from Python via subprocess: node turnstile_node.js <dx_b64> <req_token> <ua> <device_id>
 * Outputs the t field value to stdout
 */

const crypto = require('crypto');

// Parse args. --worker keeps one VM process warm and accepts JSON lines on stdin.
const workerMode = process.argv[2] === '--worker';
const [,, oneShotDx, oneShotReqToken, oneShotUa, oneShotDeviceId] = process.argv;
if (!workerMode && (!oneShotDx || !oneShotReqToken)) {
    console.error('Usage: node turnstile_node.js <dx_b64> <req_token> <ua> <device_id>');
    process.exit(1);
}

// XOR decrypt (SDK's Tt function)
function xorDecrypt(text, key) {
    let r = "";
    for (let i = 0; i < text.length; i++)
        r += String.fromCharCode(text.charCodeAt(i) ^ key.charCodeAt(i % key.length));
    return r;
}

// Mock browser globals
const mockNavigator = {
    userAgent: oneShotUa || "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/133.0.6943.88 Safari/537.36",
    language: "en-US",
    languages: ["en-US", "en"],
    platform: "Win32",
    vendor: "Google Inc.",
    vendorSub: "",
    productSub: "20030107",
    hardwareConcurrency: 8,
    maxTouchPoints: 0,
    cookieEnabled: true,
    onLine: true,
    appCodeName: "Mozilla",
    appName: "Netscape",
    pdfViewerEnabled: true,
    webdriver: false,
    deviceMemory: 8,
    connection: { effectiveType: "4g", rtt: 50, downlink: 10 },
    plugins: { length: 5 },
    mimeTypes: { length: 2 },
};

const perfOrigin = Date.now() - Math.random() * 30000;
const mockPerformance = {
    now: () => Date.now() - perfOrigin,
    timeOrigin: perfOrigin,
    memory: { jsHeapSizeLimit: 4294705152, totalJSHeapSize: 35000000, usedJSHeapSize: 25000000 },
};

const mockScreen = {
    width: 1920, height: 1080,
    availWidth: 1920, availHeight: 1040,
    colorDepth: 24, pixelDepth: 24,
};

const mockDocument = {
    scripts: [{ src: "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js" }],
    documentElement: { getAttribute: () => null },
    body: { clientWidth: 1920, clientHeight: 1080 },
    cookie: `oai-did=${oneShotDeviceId || ''}`,
    URL: "https://auth.openai.com/log-in",
    referrer: "https://chatgpt.com/",
    title: "Log in | OpenAI",
    readyState: "complete",
    hidden: false,
    visibilityState: "visible",
};

const mockLocation = {
    href: "https://auth.openai.com/log-in",
    origin: "https://auth.openai.com",
    protocol: "https:",
    host: "auth.openai.com",
    hostname: "auth.openai.com",
    pathname: "/log-in",
    search: "",
    hash: "",
    toString() { return this.href; },
};

// VM implementation (from SDK's jt + Ct + Ot functions)
const vt = new Map();
let bt = 0;

function initVM(reqToken) {
    vt.clear();
    bt = 0;
    vt.set(16, reqToken);
    vt.set(10, global);  // window = global with our mocks

    // Opcode 0: execute nested encrypted program
    vt.set(0, (regId) => {
        const dxVal = "" + vt.get(regId);
        if (!dxVal) return;
        try {
            const raw = Buffer.from(dxVal, 'base64').toString('latin1');
            const decrypted = xorDecrypt(raw, "" + vt.get(16));
            const nested = JSON.parse(decrypted);
            const saved = vt.get(9);
            vt.set(9, [...nested, ...(Array.isArray(saved) ? saved : [])]);
        } catch(e) {}
    });
    // Opcode 1: XOR
    vt.set(1, (n, e) => vt.set(n, xorDecrypt("" + vt.get(n), "" + vt.get(e))));
    // Opcode 2: SET
    vt.set(2, (n, e) => vt.set(n, e));
    // Opcode 5: PUSH/CONCAT
    vt.set(5, (n, e) => {
        const o = vt.get(n);
        Array.isArray(o) ? o.push(vt.get(e)) : vt.set(n, o + vt.get(e));
    });
    // Opcode 6: PROP ACCESS
    vt.set(6, (n, e, r) => vt.set(n, vt.get(e)?.[vt.get(r)]));
    // Opcode 7: CALL VOID
    vt.set(7, (n, ...e) => vt.get(n)?.(...e.map(a => vt.get(a))));
    // Opcode 8: COPY
    vt.set(8, (n, e) => vt.set(n, vt.get(e)));
    // Opcode 11: SCRIPT MATCH
    vt.set(11, (n, e) => {
        const pattern = vt.get(e);
        const scripts = Array.from(mockDocument.scripts || []);
        const match = scripts.map(s => s?.src?.match(pattern)).filter(m => m?.length);
        vt.set(n, (match[0] ?? [])[0] ?? null);
    });
    // Opcode 12: SELF REF
    vt.set(12, (n) => vt.set(n, vt));
    // Opcode 13: TRY CALL
    vt.set(13, (n, e, ...r) => { try { vt.get(e)?.(...r); } catch(t) { vt.set(n, "" + t); } });
    // Opcode 14: JSON.parse
    vt.set(14, (n, e) => { try { vt.set(n, JSON.parse("" + vt.get(e))); } catch(t) { vt.set(n, null); } });
    // Opcode 15: JSON.stringify
    vt.set(15, (n, e) => vt.set(n, JSON.stringify(vt.get(e))));
    // Opcode 17: CALL with return
    vt.set(17, (n, e, ...r) => {
        try {
            const t = vt.get(e)?.(...r.map(t => vt.get(t)));
            vt.set(n, t);
        } catch(t) { vt.set(n, "" + t); }
    });
    // Opcode 18: atob
    vt.set(18, (n) => vt.set(n, Buffer.from("" + vt.get(n), 'base64').toString('latin1')));
    // Opcode 19: btoa
    vt.set(19, (n) => vt.set(n, Buffer.from("" + vt.get(n), 'latin1').toString('base64')));
    // Opcode 20: IF EQUAL
    vt.set(20, (n, e, r, ...o) => vt.get(n) === vt.get(e) ? vt.get(r)?.(...o) : null);
    // Opcode 21: IF DIFF > threshold
    vt.set(21, (n, e, r, o, ...i) => Math.abs(vt.get(n) - vt.get(e)) > vt.get(r) ? vt.get(o)?.(...i) : null);
    // Opcode 22: EXEC SUB
    vt.set(22, (n, e) => {
        const saved = [...(vt.get(9) || [])];
        vt.set(9, [...e]);
        runQueue();
        vt.set(n, "" + bt);
        vt.set(9, saved);
    });
    // Opcode 23: IF DEFINED
    vt.set(23, (n, e, ...r) => void 0 !== vt.get(n) ? vt.get(e)?.(...r) : null);
    // Opcode 24: BIND
    vt.set(24, (n, e, r) => vt.set(n, vt.get(e)?.[vt.get(r)]?.bind(vt.get(e))));
    // Opcode 25, 26, 28: NOP
    vt.set(25, () => {});
    vt.set(26, () => {});
    vt.set(28, () => {});
    // Opcode 27: REMOVE/SUBTRACT
    vt.set(27, (n, e) => {
        const o = vt.get(n);
        if (Array.isArray(o)) o.splice(o.indexOf(vt.get(e)), 1);
        else vt.set(n, o - vt.get(e));
    });
    // Opcode 29: LESS THAN
    vt.set(29, (n, e, r) => vt.set(n, vt.get(e) < vt.get(r)));
    // Opcode 30: DEF FUNC
    vt.set(30, (t, n, e, r) => {
        const isArr = Array.isArray(r);
        const params = isArr ? e : [];
        const body = (isArr ? r : e) || [];
        vt.set(t, (...args) => {
            if (resolved) return;
            const saved = [...(vt.get(9) || [])];
            if (isArr) for (let i = 0; i < params.length; i++) vt.set(params[i], args[i]);
            vt.set(9, [...body]);
            runQueue();
            const result = vt.get(n);
            vt.set(9, saved);
            return result;
        });
    });
    // Opcode 33: MULTIPLY
    vt.set(33, (n, e, r) => vt.set(n, Number(vt.get(e)) * Number(vt.get(r))));
    // Opcode 34: AWAIT
    vt.set(34, (n, e) => vt.set(n, vt.get(e)));
}

let resolved = false;
let result = null;

function runQueue() {
    while (!resolved) {
        const queue = vt.get(9);
        if (!Array.isArray(queue) || queue.length === 0) break;
        const [n, ...e] = queue.shift();
        const handler = vt.get(n);
        if (typeof handler === 'function') {
            try { handler(...e); } catch(ex) {}
        }
        bt++;
        if (bt > 200000) break;  // safety limit
    }
}

// Setup global mocks
global.navigator = mockNavigator;
global.screen = mockScreen;
global.document = mockDocument;
global.performance = mockPerformance;
global.location = mockLocation;
global.localStorage = { length: 0 };
global.sessionStorage = { length: 0 };
global.history = { length: 2 };
global.chrome = { runtime: {} };
global.innerWidth = 1920;
global.innerHeight = 1080;
global.outerWidth = 1920;
global.outerHeight = 1080;
global.devicePixelRatio = 1;
global.isSecureContext = true;
global.crossOriginIsolated = false;

function executeTurnstile(dxB64, reqToken, userAgent, deviceId) {
    resolved = false;
    result = null;
    mockNavigator.userAgent = userAgent || mockNavigator.userAgent;
    mockDocument.cookie = `oai-did=${deviceId || ''}`;
    try {
        initVM(reqToken);

        // Set resolve/reject handlers
        vt.set(3, (val) => { if (!resolved) { resolved = true; result = Buffer.from("" + val, 'latin1').toString('base64'); } });
        vt.set(4, (val) => { if (!resolved) { resolved = true; result = Buffer.from("ERR:" + val, 'latin1').toString('base64'); } });

        // Decrypt and parse dx
        const raw = Buffer.from(dxB64, 'base64').toString('latin1');
        const decrypted = xorDecrypt(raw, reqToken);
        const instructions = JSON.parse(decrypted);

        // Execute
        vt.set(9, instructions);
        runQueue();

        return result || Buffer.from(bt + ": done", 'latin1').toString('base64');
    } catch(e) {
        return Buffer.from("ERR:" + e.message, 'latin1').toString('base64');
    }
}

if (workerMode) {
    const readline = require('readline');
    const reader = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
    reader.on('line', (line) => {
        let requestId = null;
        try {
            const payload = JSON.parse(line);
            requestId = payload.id ?? null;
            const value = executeTurnstile(
                String(payload.dx_b64 || ''),
                String(payload.req_token || ''),
                String(payload.ua || ''),
                String(payload.device_id || ''),
            );
            process.stdout.write(JSON.stringify({ id: requestId, result: value }) + '\n');
        } catch (e) {
            process.stdout.write(JSON.stringify({ id: requestId, error: String(e.message || e) }) + '\n');
        }
    });
} else {
    process.stdout.write(executeTurnstile(oneShotDx, oneShotReqToken, oneShotUa, oneShotDeviceId));
}
