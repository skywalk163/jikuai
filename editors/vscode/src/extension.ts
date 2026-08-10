// 极快 JiKuai · VS Code 扩展入口
//
// 职责：
//   1. 读取 `极快.pythonPath` 与 `极快.lsp.enabled` 配置；
//   2. 以 stdio 方式拉起 `python -m jikuai_lsp`（M5-P1 已交付的 LSP Server）；
//   3. 注册 `jikuai` 类型的调试适配器工厂，把 `python -m jikuai_dap`
//      作为 DAP 后端接入 VS Code 的调试 UI（M6-P3 · ADR-20）；
//   4. 注册命令面板命令 `极快.选块`（W33）：输入框收需求 → LSP
//      `workspace/executeCommand: 极快.选块` → QuickPick 候选 →
//      插入 `从 blocks.X.Y 导入 Z。` 到光标处（无编辑器时退回剪贴板）；
//   5. LSP / DAP 启动失败时降级到「仅语法高亮」，给出可操作提示，
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

/**
 * 「选块」命令 ID（与 package.json 的 contributes.commands[].command 以及
 * LSP 端 `workspace/executeCommand` 的 command 名三处对齐）。
 * LSP 契约见 `lsp/README.md` §`极快.选块`：`{需求, top?}` → `{需求, 候选[]}`。
 */
const COMMAND_SELECT_BLOCK = '极快.选块';

/** LSP `极快.选块` 命令返回的单条候选（字段真源：`src/jikuai/service/schema.py`）。 */
interface BlockCandidate {
    名称: string;
    领域: string;
    层级: number;
    描述: string;
    分数: number;
    路径?: string;
    命名空间?: string;
    /**
     * 导出名。**注意**：当前 LSP `极快.选块` 响应的候选 schema
     * （`schema.CANDIDATE_REQUIRED`）并不含此字段——见下方 `buildImportStatement`
     * 的降级说明。保留此可选字段是为将来协议补齐时无需改客户端。
     */
    导出名?: string;
}

/** LSP `极快.选块` 命令的响应信封（`schema.make_select_envelope`）。 */
interface SelectEnvelope {
    需求: string;
    候选: BlockCandidate[];
    降级说明?: string;
}

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
 * 由候选构造要插入编辑器的导入语句：`从 blocks.<领域>.<块名> 导入 <导出名>。`
 *
 * 降级说明（技术取舍，务必知悉）：
 *   LSP `极快.选块` 响应的候选 schema（`schema.CANDIDATE_REQUIRED` =
 *   名称/领域/层级/描述/分数/路径）**不含 `导出名`**。而块的目录名（`名称`）
 *   与调用用的 `导出名` 允许不同（例如「个税」块导出「缴税」）。因此这里在
 *   缺 `导出名` 时只能用 `名称` 兜底——生成的 `导入 <名称>` 对导出名与目录名
 *   同名的块正确，对二者不同的块则需用户手动改成真实导出名。
 *   彻底修复须由架构侧在候选 schema 增补 `导出名` 字段（已作为《实现反馈》
 *   回传，不在本任务擅自改协议）。
 */
function buildImportStatement(候选: BlockCandidate): string {
    const 领域 = 候选.领域;
    const 块名 = 候选.名称;
    const 导出名 = 候选.导出名 && 候选.导出名.trim() !== '' ? 候选.导出名 : 块名;
    return `从 blocks.${领域}.${块名} 导入 ${导出名}。`;
}

/**
 * 命令 `极快.选块` 的实现，完整交互链路：
 *   输入框（showInputBox 收需求）
 *     → LSP `workspace/executeCommand: 极快.选块`（{需求, top}）
 *     → QuickPick（每条显示 名称/领域/层级/分数/描述）
 *     → 选中后把导入语句插入当前编辑器光标处；无活动编辑器时退回复制到剪贴板。
 *
 * 前置：LSP 必须已启动（`极快.lsp.enabled=true` 且 `python -m jikuai_lsp` 拉起成功）。
 * 未启动时给出可操作提示，不静默失败。
 */
async function selectBlockCommand(): Promise<void> {
    if (!client) {
        void vscode.window.showWarningMessage(
            '极快选块：LSP 未启动，无法检索。请确认 `极快.lsp.enabled` 为 true '
            + '且已 `pip install -e lsp/`（详见 docs/LSP-使用.md）。',
        );
        return;
    }

    const 需求 = await vscode.window.showInputBox({
        title: '极快：选块',
        prompt: '描述你要做的事，例如「把一批数字求和再算平均」',
        placeHolder: '输入需求后回车…',
        ignoreFocusOut: true,
    });
    if (需求 === undefined || 需求.trim() === '') {
        return; // 用户取消或空输入
    }

    let envelope: SelectEnvelope | undefined;
    try {
        envelope = await client.sendRequest<SelectEnvelope>(
            'workspace/executeCommand',
            { command: COMMAND_SELECT_BLOCK, arguments: [{ 需求: 需求.trim(), top: 8 }] },
        );
    } catch (error) {
        // LSP 端对空需求 / 非法 top 回 -32602，未知命令回 -32601；两者都会走到这里。
        const detail = error instanceof Error ? error.message : String(error);
        void vscode.window.showErrorMessage(`极快选块：检索失败 —— ${detail}`);
        return;
    }

    const 候选列表 = envelope?.候选 ?? [];
    if (候选列表.length === 0) {
        void vscode.window.showInformationMessage(
            `极快选块：「${需求.trim()}」没有匹配到候选块。`,
        );
        return;
    }

    const items: (vscode.QuickPickItem & { 候选: BlockCandidate })[] = 候选列表.map((c) => ({
        label: `${c.名称}（${c.领域}）`,
        description: `L${c.层级} · 分 ${c.分数}`,
        detail: c.描述,
        候选: c,
    }));

    const picked = await vscode.window.showQuickPick(items, {
        title: `极快：选块 — ${envelope?.需求 ?? 需求.trim()}`,
        placeHolder: '选择一个块，插入 `从 blocks.… 导入 …` 语句',
        matchOnDescription: true,
        matchOnDetail: true,
    });
    if (!picked) {
        return; // 用户取消
    }

    const 语句 = buildImportStatement(picked.候选);
    const editor = vscode.window.activeTextEditor;
    if (editor) {
        await editor.edit((b) => b.insert(editor.selection.active, 语句));
        void vscode.window.showInformationMessage(`极快选块：已插入 ${语句}`);
    } else {
        await vscode.env.clipboard.writeText(语句);
        void vscode.window.showInformationMessage(
            `极快选块：无活动编辑器，已复制到剪贴板 —— ${语句}`,
        );
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

    // ---- 2. 注册命令面板命令（W33）----
    // 无条件注册：LSP 关闭 / 启动失败时命令本身仍可被调起，由 selectBlockCommand
    // 内部给出「LSP 未启动」的可操作提示。若放到 LSP 启动成功之后再注册，
    // 用户在命令面板里会看到「command not found」这种毫无线索的报错。
    context.subscriptions.push(
        vscode.commands.registerCommand(COMMAND_SELECT_BLOCK, selectBlockCommand),
    );

    // ---- 3. LSP ----
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
