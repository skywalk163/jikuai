// 极快 JiKuai · VS Code 扩展入口
//
// 职责：
//   1. 读取 `极快.pythonPath` 与 `极快.lsp.enabled` 配置；
//   2. 以 stdio 方式拉起 `python -m jikuai_lsp`（M5-P1 已交付的 LSP Server）；
//   3. LSP 启动失败时降级为「仅语法高亮」，给出可操作提示，不让扩展整体崩溃。
//
// 安全考虑：
//   - 仅执行用户显式配置的解释器路径，参数固定为 ["-m", "jikuai_lsp"]，
//     采用数组形式传参（非 shell 字符串拼接），避免命令注入；
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

/** 当前 LanguageClient 实例；未启用或启动失败时为 undefined。 */
let client: LanguageClient | undefined;

/**
 * 扩展激活入口。
 *
 * 边界条件：
 *   - `极快.lsp.enabled` 为 false：直接返回，仅保留 TextMate 高亮；
 *   - `极快.pythonPath` 为空白：回退到 "python"；
 *   - LSP 启动抛错：捕获后提示用户安装 lsp/ 包，并清理 client 引用。
 */
export async function activate(context: vscode.ExtensionContext): Promise<void> {
    const config = vscode.workspace.getConfiguration(CONFIG_SECTION);

    const lspEnabled = config.get<boolean>('lsp.enabled', true);
    if (!lspEnabled) {
        console.log('[极快] LSP 已被 `极快.lsp.enabled` 关闭，仅启用语法高亮。');
        return;
    }

    const rawPythonPath = config.get<string>('pythonPath', 'python');
    const pythonPath = (rawPythonPath ?? '').trim() || 'python';

    // 固定以模块方式启动，避免依赖 PATH 中的独立可执行文件。
    const serverOptions: ServerOptions = {
        command: pythonPath,
        args: ['-m', 'jikuai_lsp'],
        transport: TransportKind.stdio,
    };

    const clientOptions: LanguageClientOptions = {
        documentSelector: [{ scheme: 'file', language: LANGUAGE_ID }],
        outputChannelName: '极快 语言服务',
        // 语法高亮不依赖 LSP，因此 LSP 侧异常不应打扰用户编辑流程。
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
        // 交由 VS Code 在扩展停用时兜底释放（deactivate 里也会显式 stop）。
        context.subscriptions.push(client);
        console.log(`[极快] LSP 已启动：${pythonPath} -m jikuai_lsp`);
    } catch (error) {
        // 启动失败不抛出，扩展继续以「仅高亮」模式工作。
        client = undefined;
        const detail = error instanceof Error ? error.message : String(error);
        console.error('[极快] LSP 启动失败：', detail);
        void vscode.window.showWarningMessage(
            `未找到 jikuai_lsp，请先 pip install -e lsp/（已降级为仅语法高亮）。详情：${detail}`,
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
        // 进程可能已自行退出，停止失败无需向用户报错。
        console.error('[极快] LSP 停止时出现异常：', error);
    }
}
