// 极快 JiKuai · VS Code 扩展入口
//
// 职责：
//   1. 读取 `极快.pythonPath` 与 `极快.lsp.enabled` 配置；
//   2. 以 stdio 方式拉起 `python -m jikuai_lsp`（M5-P1 已交付的 LSP Server）；
//   3. 注册 `jikuai` 类型的调试适配器工厂，把 `python -m jikuai_dap`
//      作为 DAP 后端接入 VS Code 的调试 UI（M6-P3 · ADR-20）；
//   4. LSP / DAP 启动失败时降级到「仅语法高亮」，给出可操作提示，
//      不让扩展整体崩溃。
//
// 安全考虑：
//   - 仅执行用户显式配置的解释器路径，参数固定为 `-m jikuai_lsp` 或
//     `-m jikuai_dap`，采用数组形式传参（非 shell 字符串拼接），避免注入；
//   - 不发起任何外部网络请求，不读取除工作区文档之外的内容。

import * as vscode from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
    TransportKind,
} from 'vscode-languageclient/node';

/** 配置命名空间（与 package.json 的 contributes.configuration 对齐） */
const CONFIG_SECTION = '极快';

/** 语言 ID（与 package.json 的 contributes.languages 对齐） */
const LANGUAGE_ID = 'jikuai';

/** 调试类型（与 package.json 的 contributes.debuggers[].type 对齐） */
const DEBUG_TYPE = 'jikuai';

/** 当前 LanguageClient 实例；未启用或启动失败时为 undefined。 */
let client: LanguageClient | undefined;

/** 读取「极快」配置里的 Python 解释器路径，去空白后回退到 "python"。 */
function resolvePythonPath(overrideValue?: unknown): string {
    if (typeof overrideValue === 'string' && overrideValue.trim() !== '') {
        return overrideValue.trim();
    }
    const config = vscode.workspace.getConfiguration(CONFIG_SECTION);
    const raw = config.get<string>('pythonPath', 'python');
    return (raw ?? '').trim() || 'python';
}

/**
 * 极快调试适配器工厂：把 launch 请求转成 `python -m jikuai_dap` 子进程。
 *
 * 关键取舍：
 * - 每个调试会话起一个独立子进程，退出后由 VS Code 自动回收（stdio 模式）。
 * - 允许每个 launch 配置里用 `pythonPath` 覆盖扩展全局设置，便于同工作区
 *   多解释器场景。
 * - 参数用**数组**传递（非 shell 字符串），从源头杜绝命令注入。
 * - 工作目录取 `launch.cwd`；缺省时用工作区根，最后退到调试文件所在目录。
 */
class JiKuaiDebugAdapterFactory
    implements vscode.DebugAdapterDescriptorFactory {
    public createDebugAdapterDescriptor(
        session: vscode.DebugSession,
        _executable: vscode.DebugAdapterExecutable | undefined,
    ): vscode.ProviderResult<vscode.DebugAdapterDescriptor> {
        const cfg = session.configuration;
        const pythonPath = resolvePythonPath(cfg.pythonPath);

        // cwd 兜底：launch.cwd → 工作区根 → program 所在目录
        let cwd: string | undefined = typeof cfg.cwd === 'string' ? cfg.cwd : undefined;
        if (!cwd && vscode.workspace.workspaceFolders?.length) {
            cwd = vscode.workspace.workspaceFolders[0].uri.fsPath;
        }
        if (!cwd && typeof cfg.program === 'string') {
            const path = require('path') as typeof import('path');
            cwd = path.dirname(cfg.program);
        }

        return new vscode.DebugAdapterExecutable(
            pythonPath,
            ['-m', 'jikuai_dap'],
            cwd ? { cwd } : undefined,
        );
    }
}

/**
 * 调试配置解析器：当用户按 F5 而项目没有 launch.json 时，补出一份
 * 合理的默认配置，避免弹出「未找到配置」对话框。
 */
class JiKuaiDebugConfigurationProvider
    implements vscode.DebugConfigurationProvider {
    public resolveDebugConfiguration(
        _folder: vscode.WorkspaceFolder | undefined,
        config: vscode.DebugConfiguration,
        _token?: vscode.CancellationToken,
    ): vscode.ProviderResult<vscode.DebugConfiguration> {
        // 空配置（F5 直接调试当前文件）
        if (!config.type && !config.request && !config.name) {
            const editor = vscode.window.activeTextEditor;
            if (editor && editor.document.languageId === LANGUAGE_ID) {
                config.type = DEBUG_TYPE;
                config.request = 'launch';
                config.name = '调试当前 .jk 文件';
                config.program = editor.document.fileName;
                config.stopOnEntry = false;
            }
        }
        if (!config.program) {
            void vscode.window.showErrorMessage(
                '极快调试：launch 配置缺少 program，无法启动。',
            );
            return undefined; // 中止启动
        }
        return config;
    }
}

/**
 * 扩展激活入口。
 *
 * 边界条件：
 *   - `极快.lsp.enabled` 为 false：跳过 LSP，仍注册调试提供者；
 *   - `极快.pythonPath` 为空白：回退到 "python"；
 *   - LSP 启动抛错：捕获后提示用户安装 lsp/ 包，并清理 client 引用。
 */
export async function activate(context: vscode.ExtensionContext): Promise<void> {
    // ---- 1. 注册调试适配器与配置提供者（无论 LSP 是否启用都需要）----
    context.subscriptions.push(
        vscode.debug.registerDebugAdapterDescriptorFactory(
            DEBUG_TYPE, new JiKuaiDebugAdapterFactory(),
        ),
        vscode.debug.registerDebugConfigurationProvider(
            DEBUG_TYPE, new JiKuaiDebugConfigurationProvider(),
        ),
    );

    // ---- 2. LSP ----
    const config = vscode.workspace.getConfiguration(CONFIG_SECTION);
    const lspEnabled = config.get<boolean>('lsp.enabled', true);
    if (!lspEnabled) {
        console.log('[极快] LSP 已被 `极快.lsp.enabled` 关闭，仅启用语法高亮与调试。');
        return;
    }

    const pythonPath = resolvePythonPath();

    const serverOptions: ServerOptions = {
        command: pythonPath,
        args: ['-m', 'jikuai_lsp'],
        transport: TransportKind.stdio,
    };

    const clientOptions: LanguageClientOptions = {
        documentSelector: [{ scheme: 'file', language: LANGUAGE_ID }],
        outputChannelName: '极快 语言服务',
        revealOutputChannelOn: 4, // RevealOutputChannelOn.Never
    };

    client = new LanguageClient(
        'jikuaiLanguageServer',
        '极快 语言服务',
        serverOptions,
        clientOptions,
    );

    try {
        await client.start();
        context.subscriptions.push(client);
        console.log(`[极快] LSP 已启动：${pythonPath} -m jikuai_lsp`);
    } catch (error) {
        client = undefined;
        const detail = error instanceof Error ? error.message : String(error);
        console.error('[极快] LSP 启动失败：', detail);
        void vscode.window.showWarningMessage(
            `未找到 jikuai_lsp，请先 pip install -e lsp/（已降级为仅语法高亮 + 调试）。详情：${detail}`,
        );
    }
}

/** 扩展停用：停止 LanguageClient。未启动时直接返回。 */
export async function deactivate(): Promise<void> {
    if (!client) {
        return;
    }
    const stopping = client;
    client = undefined;
    try {
        await stopping.stop();
    } catch (error) {
        console.error('[极快] LSP 停止时出现异常：', error);
    }
}
