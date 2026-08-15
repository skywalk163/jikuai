# -*- coding: utf-8 -*-
"""抓 35 条需求的选响应刺激，三臂共用同一份。

刻意走真实 CLI（jikuai.main 块 选 --json）而不是直接调 Retriever，
保证被测 agent 看到的字节和它自己敲命令拿到的一致。

用法（本机 PS 5.1）：
    g:\\jikuai\\.venv\\Scripts\\python.exe g:\\jikuai\\.scratch\\agentsmd-ab\\抓刺激.py
"""
import json
import os
import subprocess
import sys

这里 = os.path.dirname(os.path.abspath(__file__))
仓库 = r'g:\jikuai'
解释器 = os.path.join(仓库, '.venv', 'Scripts', 'python.exe')


def 选(需求: str) -> dict:
    p = subprocess.run(
        [解释器, '-m', 'jikuai.main', '块', '选', 需求, '--json'],
        capture_output=True, cwd=仓库)
    if p.returncode != 0:
        raise SystemExit('选块失败 %r rc=%s err=%s'
                         % (需求, p.returncode,
                            p.stderr.decode('utf-8', 'replace')[:300]))
    return json.loads(p.stdout.decode('utf-8'))


def main() -> int:
    with open(os.path.join(这里, '用例集.json'), 'r', encoding='utf-8') as f:
        用例 = json.load(f)['用例']

    刺激 = {}
    for c in 用例:
        resp = 选(c['需求'])
        刺激[c['编号']] = {'序': c['序'], '需求': c['需求'], '选响应': resp}
        名单 = [h['名称'] for h in resp.get('候选', [])][:3]
        sys.stdout.buffer.write(
            ('%-6s %-28s -> %s\n' % (c['编号'], c['需求'], '/'.join(名单)))
            .encode('utf-8'))

    出 = os.path.join(这里, '刺激.json')
    with open(出, 'w', encoding='utf-8') as f:
        json.dump(刺激, f, ensure_ascii=False, indent=2)
    sys.stdout.buffer.write(('\n共 %d 条，已写 %s\n' % (len(刺激), 出)).encode('utf-8'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
