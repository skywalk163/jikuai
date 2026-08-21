# -*- coding: utf-8 -*-
"""G24 · 演示端点安全契约门禁（v0.28.0 W165，ADR-42）。

被 `scripts/check_stdlib_contract.py` 串起来跑（同 G16/G17/G19/G22/G23，
**不**学 G13+ 的 `except → 跳过`：解析不了就是门禁自己坏了，静默跳过等于形同虚设）。

**两套断言，缺一不可**（G23 的教训：守卫绿 ≠ 守卫在守）：

静态（AST 扫 `tools/web/demo_server.py`）：
  S1 没有以 `run_source`/`exec`/`eval` 为被调者的 Call 节点——演示端点绝不在
     服务进程内跑别人给的代码。
  S2 `允许块` 是非空的模块级 frozenset/set 字面量——空白名单等于这道闸没在守。
  S3 Token 只从 `os.environ` 取，且 `add_argument` 里不出现 token/令牌
     （命令行不收 key）。
  S4 存在 `subprocess.run(...)` 调用且带 `timeout=` 关键字——执行隔离与超时。

行为（起一个 `port=0` 的真实例，发四类请求）：
  B1 无 Token → 401
  B2 请求体带 `源码` 键 → 400
  B3 白名单外的块 → 400
  B4 数据集路径逃逸（`../`）→ 400

为什么行为断言不可省：静态扫只能看见「代码里有那几行」，看不见「那几行真的在
请求路径上被执行」。把 `_核验令牌` 的调用从 `_GET`/`_POST` 里删掉，S1-S4 全绿。
为什么静态断言不可省：行为断言可以被一个「凡不认识的键就拒」的粗暴实现全部满足，
而那时白名单已经没了。

用法：`python scripts/check_demo_endpoint_contract.py [--quiet]`，退 0 / 1。
"""

import argparse
import ast
import http.client
import importlib.util
import json
import os
import sys
import threading
import urllib.parse

HERE = os.path.abspath(os.path.dirname(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, '..'))
DEMO_PY = os.path.join(REPO_ROOT, 'tools', 'web', 'demo_server.py')

#: 禁止在服务进程内出现的被调者名。刻意**不**含 `compile`——`re.compile` 是正常
#: 用法，按被调者名字扫会假红（W164 写测试时真栽过一次）。
_禁调 = frozenset({'run_source', 'exec', 'eval'})

#: 门禁自用的测试令牌。必须纯 ASCII（HTTP 头值只收 latin-1）。
_令牌 = 'g24-gate-token-0123456789'


def _被调名(节点):
    if isinstance(节点.func, ast.Name):
        return 节点.func.id
    if isinstance(节点.func, ast.Attribute):
        return 节点.func.attr
    return None


# ---- 静态断言 ----------------------------------------------------------

def 静态检查(源码路径=DEMO_PY):
    """返回问题描述列表（空 = 通过）。"""
    问题 = []
    if not os.path.isfile(源码路径):
        return ['找不到演示端点源文件：%s' % 源码路径]
    with open(源码路径, 'r', encoding='utf-8') as f:
        文本 = f.read()
    try:
        树 = ast.parse(文本, filename=源码路径)
    except SyntaxError as e:
        return ['演示端点源文件语法错误：%s' % e]

    # S1：没有进程内执行
    for 节点 in ast.walk(树):
        if isinstance(节点, ast.Call) and _被调名(节点) in _禁调:
            问题.append('S1 行 %d 出现进程内执行调用 %s()——演示端点必须走子进程'
                      % (节点.lineno, _被调名(节点)))

    # S2：允许块 非空集合字面量
    白名单节点 = None
    for 节点 in ast.walk(树):
        if isinstance(节点, ast.Assign):
            for 目标 in 节点.targets:
                if isinstance(目标, ast.Name) and 目标.id == '允许块':
                    白名单节点 = 节点.value
    if 白名单节点 is None:
        问题.append('S2 找不到模块级 `允许块` 赋值——块白名单是演示端点的信任边界')
    else:
        元素 = None
        if isinstance(白名单节点, ast.Set):
            元素 = 白名单节点.elts
        elif (isinstance(白名单节点, ast.Call)
              and _被调名(白名单节点) == 'frozenset'
              and 白名单节点.args
              and isinstance(白名单节点.args[0], ast.Set)):
            元素 = 白名单节点.args[0].elts
        if 元素 is None:
            问题.append('S2 `允许块` 不是集合字面量（要能静态看清放行了哪些块）')
        elif not 元素:
            问题.append('S2 `允许块` 是空集——空白名单等于这道闸没在守')

    # S3：Token 只从环境变量取，命令行不收 key
    if 'os.environ' not in 文本:
        问题.append('S3 没有从 os.environ 读取 Token 的痕迹')
    for i, 行 in enumerate(文本.splitlines(), 1):
        if 'add_argument' in 行 and ('token' in 行.lower() or '令牌' in 行):
            问题.append('S3 行 %d 的命令行参数收了 Token：%s' % (i, 行.strip()))

    # S4：subprocess.run 且带 timeout
    子进程调用 = [n for n in ast.walk(树)
              if isinstance(n, ast.Call) and _被调名(n) == 'run'
              and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name)
              and n.func.value.id == 'subprocess']
    if not 子进程调用:
        问题.append('S4 找不到 subprocess.run(...)——执行隔离靠它')
    elif not any(any(k.arg == 'timeout' for k in n.keywords) for n in 子进程调用):
        问题.append('S4 subprocess.run 没带 timeout=——超时闸缺失')

    return 问题


# ---- 行为断言 ----------------------------------------------------------

def _加载演示(路径=DEMO_PY):
    spec = importlib.util.spec_from_file_location('_g24_demo_server', 路径)
    if spec is None or spec.loader is None:
        raise RuntimeError('无法加载 %s' % 路径)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _发(地址, 方法, 路径, body=None, 令牌=_令牌):
    host, port = 地址
    conn = http.client.HTTPConnection(host, port, timeout=30)
    try:
        头 = {}
        数据 = None
        if body is not None:
            数据 = json.dumps(body, ensure_ascii=False).encode('utf-8')
            头['Content-Type'] = 'application/json; charset=utf-8'
        if 令牌 is not None:
            头['Authorization'] = 'Bearer ' + 令牌
        conn.request(方法, urllib.parse.quote(路径), body=数据, headers=头)
        resp = conn.getresponse()
        return resp.status, resp.read().decode('utf-8', 'replace')
    finally:
        conn.close()


def _样例方案():
    """一份形状合法的制造域方案，供反例改造。"""
    return {
        '需求': 'G24 行为断言',
        '共享': [
            {'名': '赵路', '值': '“赛题/chatbi/数据集/fact_energy_usage.csv”'},
        ],
        '步骤': [
            {'块': '表载入', '领域': '制造', '导出名': '读表', '参数': ['赵路']},
        ],
    }


def 行为检查(路径=DEMO_PY):
    """起一个真实例，发四类请求。返回问题描述列表（空 = 通过）。"""
    问题 = []
    旧 = os.environ.get('JIKUAI_DEMO_TOKEN')
    os.environ['JIKUAI_DEMO_TOKEN'] = _令牌
    demo = None
    srv = None
    t = None
    try:
        demo = _加载演示(路径)
        srv = demo.build_server('127.0.0.1', 0)
        t = threading.Thread(target=srv.serve_forever, name='g24-demo', daemon=True)
        t.start()
        地址 = (srv.server_address[0], srv.server_address[1])

        # B1 无 Token → 401
        状态, 体 = _发(地址, 'GET', '/演示/白名单', 令牌=None)
        if 状态 != 401:
            问题.append('B1 无 Token 应回 401，实回 %d：%s' % (状态, 体[:200]))

        # B2 带「源码」键 → 400
        状态, 体 = _发(地址, 'POST', '/演示/跑',
                    body={'方案': _样例方案(), '源码': '打印 1。'})
        if 状态 != 400:
            问题.append('B2 请求体带「源码」应回 400，实回 %d：%s' % (状态, 体[:200]))

        # B3 白名单外的块 → 400
        方案 = _样例方案()
        方案['步骤'][0] = {'块': '个税', '领域': '财务', '导出名': '缴税',
                        '参数': ['赵路']}
        状态, 体 = _发(地址, 'POST', '/演示/跑', body={'方案': 方案})
        if 状态 != 400 or '白名单' not in 体:
            问题.append('B3 白名单外的块应回 400 且理由含「白名单」，实回 %d：%s'
                      % (状态, 体[:200]))

        # B4 路径逃逸 → 400
        方案 = _样例方案()
        方案['共享'][0]['值'] = '“赛题/chatbi/数据集/../../../pyproject.toml”'
        状态, 体 = _发(地址, 'POST', '/演示/跑', body={'方案': 方案})
        if 状态 != 400 or '越界' not in 体:
            问题.append('B4 路径逃逸应回 400 且理由含「越界」，实回 %d：%s'
                      % (状态, 体[:200]))
    except Exception as e:                                    # noqa: BLE001
        问题.append('行为断言执行失败：%s：%s' % (type(e).__name__, e))
    finally:
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        if t is not None:
            t.join(timeout=5)
        if 旧 is None:
            os.environ.pop('JIKUAI_DEMO_TOKEN', None)
        else:
            os.environ['JIKUAI_DEMO_TOKEN'] = 旧
    return 问题


def main(argv=None):
    p = argparse.ArgumentParser(description='G24 演示端点安全契约门禁')
    p.add_argument('--quiet', action='store_true', help='通过时不打输出')
    p.add_argument('--源码', dest='源码', default=DEMO_PY,
                   help='被检的演示端点源文件（反例测试用）')
    args = p.parse_args(argv)

    问题 = 静态检查(args.源码) + 行为检查(args.源码)
    if 问题:
        print('G24 演示端点安全契约不通过（%d 处）：' % len(问题))
        for 条 in 问题:
            print('  - %s' % 条)
        return 1
    if not args.quiet:
        print('G24 演示端点安全契约通过（静态 4 项 + 行为 4 项）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
