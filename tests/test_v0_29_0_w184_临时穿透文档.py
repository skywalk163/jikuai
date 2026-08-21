# -*- coding: utf-8 -*-
"""v0.29.0 W184 · 临时穿透只做到「本地演示」这一档的文档守卫。

本轮拍板（A 档）：临时内网穿透**只作本地演示手段**，v0.28.0「不做公网部署」的结论
**不推翻**，赛题交付物「在线访问链接」一栏**仍然如实空着**，那两条 v0.28.0 W178 的
诚实性守卫测试**一行未改**。

这里守的是「文档没被悄悄写成公网部署」和「清单里没被填进一个临时地址」。后者尤其
要守：临时穿透链接随进程生死、随隧道重连换地址，填进交付物等于虚报一个「稳定可
访问」的承诺——而那正好是 v0.28.0 反向度量的那一条。
"""

import os
import re
import unittest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
安全边界 = os.path.join(_REPO, 'docs', '安全边界.md')
交付物清单 = os.path.join(_REPO, '赛题', 'chatbi', '产出', '交付物清单.md')


def _读(路径):
    with open(路径, encoding='utf-8') as f:
        return f.read()


class 穿透文档(unittest.TestCase):
    def test_安全边界有穿透一节且标明只为本地演示(self):
        正文 = _读(安全边界)
        self.assertIn('6.3', 正文)
        self.assertIn('临时穿透', 正文)
        self.assertIn('不做公网正式部署', 正文)

    def test_穿透一节明确不许改监听地址(self):
        """为了穿透把 `127.0.0.1` 改成 `0.0.0.0`，等于把闸从一条隧道放宽到整个网段。"""
        正文 = _读(安全边界)
        段 = 正文.split('### 6.3')[1].split('## 7.')[0]
        self.assertIn('0.0.0.0', 段)
        self.assertIn('127.0.0.1', 段)
        self.assertIn('不要', 段)

    def test_穿透一节要求先设Token并演示后轮换(self):
        段 = _读(安全边界).split('### 6.3')[1].split('## 7.')[0]
        self.assertIn('JIKUAI_DEMO_TOKEN', 段)
        self.assertIn('轮换', 段)

    def test_穿透一节如实写明没有限流(self):
        """隧道不提供限流防刷，这条对系统不利但必须写。"""
        段 = _读(安全边界).split('### 6.3')[1].split('## 7.')[0]
        self.assertIn('限流', 段)


class 交付物清单未被改口(unittest.TestCase):
    """A 档的落点：清单一个字都不该因为「能穿透了」而变。"""

    def test_清单仍写着不做公网部署与如实空着(self):
        正文 = _读(交付物清单)
        self.assertIn('不做公网部署', 正文)
        self.assertIn('如实空着', 正文)

    def test_清单里没有被填进任何隧道地址(self):
        """临时地址随隧道重连就变，填进交付物就是虚报稳定可访问。"""
        正文 = _读(交付物清单)
        for 模式 in (r'https?://[^\s)]*trycloudflare',
                     r'https?://[^\s)]*ngrok',
                     r'https?://127\.0\.0\.1',
                     r'https?://localhost'):
            self.assertIsNone(re.search(模式, 正文, re.I),
                              '清单里出现了本机/隧道地址：%s' % 模式)


if __name__ == '__main__':
    unittest.main()
