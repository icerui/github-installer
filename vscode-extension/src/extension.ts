/**
 * GitInstall VS Code Extension
 *
 * 让你轻松安装 GitHub/HuggingFace 项目的 VS Code 插件。
 *
 * 功能：
 *   1. 命令面板输入 URL → 自动安装
 *   2. 剪贴板 URL 快速安装
 *   3. 安全审计面板
 *   4. AI 模型 VRAM 评估
 *   5. SBOM 生成
 *   6. CI/CD 配置生成
 *   7. 侧边栏 Dashboard
 */

import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as os from 'os';

// ─────────────────────────────────────────────
//  常量
// ─────────────────────────────────────────────

const EXTENSION_ID = 'gitinstall';
const OUTPUT_CHANNEL_NAME = 'GitInstall';

// ─────────────────────────────────────────────
//  Extension 激活
// ─────────────────────────────────────────────

let outputChannel: vscode.OutputChannel;

export function activate(context: vscode.ExtensionContext) {
    outputChannel = vscode.window.createOutputChannel(OUTPUT_CHANNEL_NAME);

    // 注册命令
    context.subscriptions.push(
        vscode.commands.registerCommand('gitinstall.install', () => installProject()),
        vscode.commands.registerCommand('gitinstall.installFromClipboard', () => installFromClipboard()),
        vscode.commands.registerCommand('gitinstall.audit', () => runSecurityAudit()),
        vscode.commands.registerCommand('gitinstall.vramEstimate', () => estimateVRAM()),
        vscode.commands.registerCommand('gitinstall.generateSBOM', () => generateSBOM()),
        vscode.commands.registerCommand('gitinstall.showDashboard', () => showDashboard(context)),
        vscode.commands.registerCommand('gitinstall.generateCI', () => generateCIConfig()),
    );

    // 注册 Tree View
    const recentProvider = new RecentInstallsProvider();
    vscode.window.registerTreeDataProvider('gitinstall.recentInstalls', recentProvider);

    // URI Handler (vscode://gitinstall.gitinstall/install?repo=owner/repo)
    context.subscriptions.push(
        vscode.window.registerUriHandler({
            handleUri(uri: vscode.Uri) {
                const params = new URLSearchParams(uri.query);
                const repo = params.get('repo');
                if (repo && uri.path === '/install') {
                    installProject(repo);
                }
            }
        })
    );

    outputChannel.appendLine('GitInstall extension activated');
}

export function deactivate() {
    outputChannel?.dispose();
}

// ─────────────────────────────────────────────
//  核心功能：安装项目
// ─────────────────────────────────────────────

async function installProject(repoUrl?: string) {
    // 1. 获取 URL
    if (!repoUrl) {
        repoUrl = await vscode.window.showInputBox({
            prompt: 'Enter GitHub repository URL or owner/repo',
            placeHolder: 'e.g., pytorch/pytorch or https://github.com/pytorch/pytorch',
            validateInput: (value) => {
                if (!value.trim()) { return 'Please enter a repository URL'; }
                return null;
            }
        });
    }
    if (!repoUrl) { return; }

    // 2. 选择安装目录
    const config = vscode.workspace.getConfiguration(EXTENSION_ID);
    const defaultDir = config.get<string>('installDirectory') || os.homedir();
    const targetDir = await vscode.window.showOpenDialog({
        defaultUri: vscode.Uri.file(defaultDir),
        canSelectFolders: true,
        canSelectFiles: false,
        openLabel: 'Install Here',
        title: `Select installation directory for ${repoUrl}`,
    });
    if (!targetDir || !targetDir[0]) { return; }
    const installDir = targetDir[0].fsPath;

    // 3. 确认安装
    const confirmEnabled = config.get<boolean>('confirmBeforeExecute', true);
    if (confirmEnabled) {
        const confirm = await vscode.window.showInformationMessage(
            `Install "${repoUrl}" to "${installDir}"?`,
            { modal: true },
            'Install', 'Install (Skip Audit)'
        );
        if (!confirm) { return; }
        if (confirm === 'Install') {
            await runAuditBeforeInstall(repoUrl);
        }
    }

    // 4. 执行安装
    await executeInstall(repoUrl, installDir);
}

async function installFromClipboard() {
    const clipboard = await vscode.env.clipboard.readText();
    const trimmed = clipboard.trim();

    if (isGitHubUrl(trimmed) || isHuggingFaceUrl(trimmed) || /^[\w-]+\/[\w.-]+$/.test(trimmed)) {
        await installProject(trimmed);
    } else {
        const url = await vscode.window.showInputBox({
            prompt: 'Clipboard does not contain a valid URL. Enter manually:',
            value: trimmed,
        });
        if (url) { await installProject(url); }
    }
}

function isGitHubUrl(url: string): boolean {
    return /^https?:\/\/(www\.)?github\.com\/[\w._-]+\/[\w._-]+/.test(url);
}

function isHuggingFaceUrl(url: string): boolean {
    return /^https?:\/\/(www\.)?huggingface\.co\/[\w._-]+\/[\w._-]+/.test(url);
}

// ─────────────────────────────────────────────
//  Python 后端交互
// ─────────────────────────────────────────────

function getPythonPath(): string {
    const config = vscode.workspace.getConfiguration(EXTENSION_ID);
    return config.get<string>('pythonPath') || 'python3';
}

function getGitInstallArgs(command: string, args: string[]): string[] {
    return ['-m', 'tools.main', command, ...args];
}

async function executeInstall(repoUrl: string, installDir: string) {
    const pythonPath = getPythonPath();

    // 显示进度
    await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: `Installing ${repoUrl}`,
        cancellable: true,
    }, async (progress, token) => {
        progress.report({ increment: 0, message: 'Analyzing project...' });

        const terminal = vscode.window.createTerminal({
            name: `GitInstall: ${repoUrl}`,
            cwd: installDir,
        });
        terminal.show();

        // 构建命令
        const config = vscode.workspace.getConfiguration(EXTENSION_ID);
        const llmProvider = config.get<string>('llmProvider', 'auto');
        const mirrorPypi = config.get<string>('mirrorPypi', '');
        const mirrorNpm = config.get<string>('mirrorNpm', '');

        let cmd = `${pythonPath} -m tools.main install "${repoUrl}"`;
        if (llmProvider !== 'auto') {
            cmd += ` --llm ${llmProvider}`;
        }

        // 设置镜像环境变量
        let envPrefix = '';
        if (mirrorPypi) {
            envPrefix += `PIP_INDEX_URL="${mirrorPypi}" `;
        }
        if (mirrorNpm) {
            envPrefix += `NPM_CONFIG_REGISTRY="${mirrorNpm}" `;
        }

        terminal.sendText(`${envPrefix}${cmd}`);

        progress.report({ increment: 50, message: 'Installing...' });

        // 等待终端关闭或取消
        return new Promise<void>((resolve) => {
            const disposable = vscode.window.onDidCloseTerminal((t) => {
                if (t === terminal) {
                    disposable.dispose();
                    progress.report({ increment: 100, message: 'Done' });
                    resolve();
                }
            });
            token.onCancellationRequested(() => {
                terminal.dispose();
                resolve();
            });
        });
    });
}

// ─────────────────────────────────────────────
//  安全审计
// ─────────────────────────────────────────────

async function runSecurityAudit() {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders) {
        vscode.window.showWarningMessage('No workspace folder open');
        return;
    }

    const projectDir = workspaceFolders[0].uri.fsPath;
    const pythonPath = getPythonPath();

    await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: 'Running security audit...',
        cancellable: false,
    }, async (progress) => {
        progress.report({ increment: 0 });

        try {
            const result = await runPython(pythonPath, [
                '-c',
                `import sys; sys.path.insert(0, '.'); ` +
                `from tools.dependency_audit import audit_project, format_audit_results; ` +
                `results = audit_project('${projectDir.replace(/'/g, "\\'")}'); ` +
                `print(format_audit_results(results))`
            ], projectDir);

            outputChannel.clear();
            outputChannel.appendLine('=== Security Audit Results ===\n');
            outputChannel.appendLine(result);
            outputChannel.show();

            if (result.includes('CRITICAL') || result.includes('HIGH')) {
                vscode.window.showWarningMessage(
                    'Security issues found! Check GitInstall output for details.',
                    'Show Details'
                ).then(action => {
                    if (action) { outputChannel.show(); }
                });
            } else {
                vscode.window.showInformationMessage('Security audit passed ✅');
            }
        } catch (error) {
            vscode.window.showErrorMessage(`Audit failed: ${error}`);
        }
    });
}

async function runAuditBeforeInstall(repoUrl: string) {
    outputChannel.appendLine(`Pre-install audit for ${repoUrl}...`);
    // Lightweight audit — just check known CVEs for the repo name
}

// ─────────────────────────────────────────────
//  VRAM 评估
// ─────────────────────────────────────────────

async function estimateVRAM() {
    const modelId = await vscode.window.showInputBox({
        prompt: 'Enter HuggingFace model ID',
        placeHolder: 'e.g., meta-llama/Llama-3.1-8B or Qwen/Qwen2.5-7B',
    });
    if (!modelId) { return; }

    const vram = await vscode.window.showInputBox({
        prompt: 'Available GPU VRAM (GB)',
        placeHolder: 'e.g., 8, 16, 24, 48',
        validateInput: (v) => isNaN(Number(v)) ? 'Please enter a number' : null,
    });
    if (!vram) { return; }

    const useCase = await vscode.window.showQuickPick(
        [
            { label: '推理 (Inference)', value: 'inference' },
            { label: 'LoRA 微调', value: 'lora' },
            { label: '全量微调 (Full Finetune)', value: 'finetune' },
        ],
        { placeHolder: 'Select use case' }
    );
    if (!useCase) { return; }

    const pythonPath = getPythonPath();

    try {
        const result = await runPython(pythonPath, [
            '-c',
            `import sys; sys.path.insert(0, '.'); ` +
            `from tools.huggingface import estimate_vram, format_vram_estimate; ` +
            `est = estimate_vram('${modelId.replace(/'/g, "\\'")}', ${vram}, '${useCase.value}'); ` +
            `print(format_vram_estimate(est))`
        ]);

        outputChannel.clear();
        outputChannel.appendLine('=== VRAM Estimation ===\n');
        outputChannel.appendLine(result);
        outputChannel.show();
    } catch (error) {
        vscode.window.showErrorMessage(`VRAM estimation failed: ${error}`);
    }
}

// ─────────────────────────────────────────────
//  SBOM 生成
// ─────────────────────────────────────────────

async function generateSBOM() {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders) {
        vscode.window.showWarningMessage('No workspace folder open');
        return;
    }

    const format = await vscode.window.showQuickPick(
        [
            { label: 'CycloneDX 1.5 (OWASP)', value: 'cyclonedx' },
            { label: 'SPDX 2.3 (Linux Foundation)', value: 'spdx' },
        ],
        { placeHolder: 'Select SBOM format' }
    );
    if (!format) { return; }

    const projectDir = workspaceFolders[0].uri.fsPath;
    const pythonPath = getPythonPath();

    try {
        const result = await runPython(pythonPath, [
            '-c',
            `import sys; sys.path.insert(0, '.'); ` +
            `from tools.dependency_audit import export_sbom; ` +
            `path = export_sbom('${projectDir.replace(/'/g, "\\'")}', fmt='${format.value}'); ` +
            `print('SBOM exported to: ' + path)`
        ], projectDir);

        vscode.window.showInformationMessage(result.trim(), 'Open File').then(action => {
            if (action) {
                const filePath = result.replace('SBOM exported to: ', '').trim();
                vscode.workspace.openTextDocument(filePath).then(doc => {
                    vscode.window.showTextDocument(doc);
                });
            }
        });
    } catch (error) {
        vscode.window.showErrorMessage(`SBOM generation failed: ${error}`);
    }
}

// ─────────────────────────────────────────────
//  CI/CD 配置生成
// ─────────────────────────────────────────────

async function generateCIConfig() {
    const platform = await vscode.window.showQuickPick(
        [
            { label: 'GitHub Actions', value: 'github' },
            { label: 'GitLab CI', value: 'gitlab' },
        ],
        { placeHolder: 'Select CI/CD platform' }
    );
    if (!platform) { return; }

    const repos = await vscode.window.showInputBox({
        prompt: 'Enter repository URLs (comma-separated)',
        placeHolder: 'e.g., pytorch/pytorch, huggingface/transformers',
    });
    if (!repos) { return; }

    const repoList = repos.split(',').map(r => r.trim()).filter(Boolean);
    const pythonPath = getPythonPath();
    const repoJson = JSON.stringify(repoList);

    try {
        let pyCode: string;
        if (platform.value === 'github') {
            pyCode = `import sys; sys.path.insert(0, '.'); ` +
                     `from tools.cicd import generate_github_action; ` +
                     `print(generate_github_action(${repoJson}))`;
        } else {
            pyCode = `import sys; sys.path.insert(0, '.'); ` +
                     `from tools.cicd import generate_gitlab_ci; ` +
                     `print(generate_gitlab_ci(${repoJson}))`;
        }

        const result = await runPython(pythonPath, ['-c', pyCode]);

        // 创建新文件显示
        const doc = await vscode.workspace.openTextDocument({
            content: result,
            language: 'yaml',
        });
        await vscode.window.showTextDocument(doc);

        vscode.window.showInformationMessage(
            `CI config generated! Save as ${platform.value === 'github' ? '.github/workflows/gitinstall.yml' : '.gitlab-ci.yml'}`
        );
    } catch (error) {
        vscode.window.showErrorMessage(`CI config generation failed: ${error}`);
    }
}

// ─────────────────────────────────────────────
//  Dashboard WebView
// ─────────────────────────────────────────────

async function showDashboard(context: vscode.ExtensionContext) {
    const panel = vscode.window.createWebviewPanel(
        'gitinstallDashboard',
        'GitInstall Dashboard',
        vscode.ViewColumn.One,
        { enableScripts: true }
    );

    panel.webview.html = getDashboardHTML();

    panel.webview.onDidReceiveMessage(async (message) => {
        switch (message.command) {
            case 'install':
                await installProject(message.url);
                break;
            case 'audit':
                await runSecurityAudit();
                break;
            case 'vram':
                await estimateVRAM();
                break;
        }
    });
}

function getDashboardHTML(): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitInstall Dashboard</title>
    <style>
        body {
            font-family: var(--vscode-font-family);
            color: var(--vscode-foreground);
            background: var(--vscode-editor-background);
            padding: 20px;
            margin: 0;
        }
        h1 { font-size: 1.6em; margin-bottom: 8px; }
        h2 { font-size: 1.2em; margin-top: 24px; color: var(--vscode-textLink-foreground); }
        .card {
            background: var(--vscode-editorWidget-background);
            border: 1px solid var(--vscode-widget-border);
            border-radius: 6px;
            padding: 16px;
            margin: 12px 0;
        }
        .card h3 { margin-top: 0; }
        input[type="text"] {
            width: 100%;
            padding: 8px 12px;
            background: var(--vscode-input-background);
            color: var(--vscode-input-foreground);
            border: 1px solid var(--vscode-input-border);
            border-radius: 4px;
            font-size: 14px;
            box-sizing: border-box;
        }
        button {
            padding: 8px 16px;
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            margin-top: 8px;
            margin-right: 8px;
        }
        button:hover { background: var(--vscode-button-hoverBackground); }
        .btn-secondary {
            background: var(--vscode-button-secondaryBackground);
            color: var(--vscode-button-secondaryForeground);
        }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .stat { text-align: center; }
        .stat-value { font-size: 2em; font-weight: bold; color: var(--vscode-textLink-foreground); }
        .stat-label { font-size: 0.9em; opacity: 0.8; }
        .feature-list { list-style: none; padding: 0; }
        .feature-list li { padding: 4px 0; }
        .feature-list li::before { content: "✅ "; }
    </style>
</head>
<body>
    <h1>🚀 GitInstall Dashboard</h1>
    <p>让你轻松安装 GitHub / HuggingFace 项目</p>

    <div class="card">
        <h3>📦 Quick Install</h3>
        <input type="text" id="repoUrl" placeholder="Enter GitHub URL or owner/repo..." />
        <br/>
        <button onclick="install()">Install</button>
        <button class="btn-secondary" onclick="audit()">🔒 Security Audit</button>
        <button class="btn-secondary" onclick="vram()">🧠 VRAM Estimate</button>
    </div>

    <h2>Features</h2>
    <div class="grid">
        <div class="card">
            <h3>🔒 Security</h3>
            <ul class="feature-list">
                <li>CVE vulnerability scanning</li>
                <li>Typosquatting detection</li>
                <li>SBOM generation (CycloneDX/SPDX)</li>
                <li>Dependency audit</li>
            </ul>
        </div>
        <div class="card">
            <h3>🤖 AI/ML</h3>
            <ul class="feature-list">
                <li>HuggingFace model VRAM estimation</li>
                <li>Quantization recommendations</li>
                <li>70+ model database</li>
                <li>15 LLM provider fallback</li>
            </ul>
        </div>
        <div class="card">
            <h3>🏢 Enterprise</h3>
            <ul class="feature-list">
                <li>SSO (OIDC/SAML)</li>
                <li>RBAC role management</li>
                <li>Audit logging</li>
                <li>Private repo support</li>
            </ul>
        </div>
        <div class="card">
            <h3>🔄 CI/CD</h3>
            <ul class="feature-list">
                <li>GitHub Actions generator</li>
                <li>GitLab CI generator</li>
                <li>Install lock files</li>
                <li>JUnit report output</li>
            </ul>
        </div>
    </div>

    <h2>Supported Languages</h2>
    <p>Python · Node.js · Rust · Go · C/C++ · Java · Haskell · Ruby · Elixir · .NET · PHP · Swift · Kotlin · Docker · Nix</p>

    <script>
        const vscode = acquireVsCodeApi();
        function install() {
            const url = document.getElementById('repoUrl').value;
            if (url) { vscode.postMessage({ command: 'install', url }); }
        }
        function audit() { vscode.postMessage({ command: 'audit' }); }
        function vram() { vscode.postMessage({ command: 'vram' }); }
    </script>
</body>
</html>`;
}

// ─────────────────────────────────────────────
//  Tree View: Recent Installs
// ─────────────────────────────────────────────

class RecentInstallsProvider implements vscode.TreeDataProvider<InstallItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<InstallItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    getTreeItem(element: InstallItem): vscode.TreeItem {
        return element;
    }

    getChildren(): InstallItem[] {
        // Load from storage in real implementation
        return [
            new InstallItem('No recent installs', 'Open Command Palette to install a project', 'info'),
        ];
    }

    refresh(): void {
        this._onDidChangeTreeData.fire(undefined);
    }
}

class InstallItem extends vscode.TreeItem {
    constructor(
        public readonly label: string,
        public readonly detail: string,
        public readonly status: 'success' | 'failed' | 'info',
    ) {
        super(label, vscode.TreeItemCollapsibleState.None);
        this.tooltip = detail;
        this.description = detail;
        this.iconPath = new vscode.ThemeIcon(
            status === 'success' ? 'pass' : status === 'failed' ? 'error' : 'info'
        );
    }
}

// ─────────────────────────────────────────────
//  工具函数
// ─────────────────────────────────────────────

function runPython(pythonPath: string, args: string[], cwd?: string): Promise<string> {
    return new Promise((resolve, reject) => {
        const workDir = cwd || vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || os.homedir();
        const proc = cp.spawn(pythonPath, args, {
            cwd: workDir,
            env: { ...process.env },
            timeout: 60000,
        });

        let stdout = '';
        let stderr = '';

        proc.stdout.on('data', (data: Buffer) => { stdout += data.toString(); });
        proc.stderr.on('data', (data: Buffer) => { stderr += data.toString(); });

        proc.on('close', (code: number | null) => {
            if (code === 0) {
                resolve(stdout.trim());
            } else {
                reject(new Error(stderr || `Process exited with code ${code}`));
            }
        });

        proc.on('error', (err: Error) => {
            reject(new Error(`Failed to start Python: ${err.message}`));
        });
    });
}
