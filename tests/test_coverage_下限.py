# -*- coding: utf-8 -*-
"""逐文件覆盖率下限（点阈值）的判定逻辑（W94 残留 · v0.22.0）。

这里**不测覆盖率**，只测「拿到一份 coverage JSON 后怎么判」。真实测量要跑全量
测试（几分钟），不适合放进单元测试；但判定逻辑一旦写错，门禁会静默放行 ——
v0.22.0 刚在 CI 的 AOT 零 skip 守卫上踩过同一类坑（守卫是绿的，守了个空），
所以这条链路的判定部分必须有回归保护。
"""

import importlib.util
import json
import os

import pytest

仓库根 = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))


def _载入编排器():
    """按路径载入 `scripts/coverage_baseline.py`。

    scripts/ 不是包、也不在 sys.path 上，正常 import 进不来；测试里按文件路径
    载入比往 sys.path 里塞目录干净（不污染其他用例的导入环境）。
    """
    路径 = os.path.join(仓库根, 'scripts', 'coverage_baseline.py')
    规格 = importlib.util.spec_from_file_location('_coverage_baseline', 路径)
    模块 = importlib.util.module_from_spec(规格)
    规格.loader.exec_module(模块)
    return 模块


@pytest.fixture(scope='module')
def 编排器():
    return _载入编排器()


@pytest.fixture
def 写JSON(tmp_path):
    """造一份最小 coverage JSON（只保留判定用得到的字段）。"""
    def 写(文件表):
        路径 = tmp_path / 'cov.json'
        路径.write_text(json.dumps({'files': 文件表}), encoding='utf-8')
        return str(路径)
    return 写


def _条目(百分比):
    return {'summary': {'percent_covered': 百分比}}


class Test下限表:
    def test_下限文件存在且可解析(self, 编排器):
        下限 = 编排器._读下限()
        assert 下限, 'docs/覆盖率下限.json 读出来是空的——门禁会静默全绿'
        assert all(isinstance(v, (int, float)) for v in 下限.values())

    def test_键必须是仓库内真实存在的文件(self, 编排器):
        """下限表点名的文件若被改名/删除而没同步，门禁只会在测量时才炸。

        这条测试让「表和现实脱节」在秒级暴露。
        """
        缺失 = [键 for 键 in 编排器._读下限()
                if not os.path.exists(os.path.join(仓库根, 键))]
        assert not 缺失, '下限表点名的文件不存在：%s' % 缺失

    def test_键用posix分隔符(self, 编排器):
        """判定时统一成 posix 再比，表里也必须是 posix，否则永远匹配不上。"""
        assert all('\\' not in 键 for 键 in 编排器._读下限())


class Test判定:
    def test_全部达标(self, 编排器, 写JSON):
        下限 = 编排器._读下限()
        路径 = 写JSON({键: _条目(值 + 5) for 键, 值 in 下限.items()})
        达标, 违规 = 编排器._检查逐文件下限(路径)
        assert 达标 and 违规 == []

    def test_恰好等于下限算达标(self, 编排器, 写JSON):
        """下限是「含」语义。浮点比较必须留容差，否则 78.0 会被判成 < 78.0。"""
        下限 = 编排器._读下限()
        路径 = 写JSON({键: _条目(值) for 键, 值 in 下限.items()})
        达标, 违规 = 编排器._检查逐文件下限(路径)
        assert 达标, '边界值被误判为违规：%s' % (违规,)

    def test_跌破下限要报出实测值与地板(self, 编排器, 写JSON):
        下限 = 编排器._读下限()
        目标 = sorted(下限)[0]
        文件表 = {键: _条目(值 + 5) for 键, 值 in 下限.items()}
        文件表[目标] = _条目(下限[目标] - 0.1)
        达标, 违规 = 编排器._检查逐文件下限(写JSON(文件表))
        assert not 达标
        assert 违规 == [(目标, 下限[目标] - 0.1, 下限[目标])]

    def test_报告里没有该文件等同触底(self, 编排器, 写JSON):
        """文件被改名或整体没被 import 时，coverage JSON 里根本没有这一项。

        「查不到」必须判失败而不是跳过 —— 否则删掉一个文件就能绕过它的下限。
        """
        下限 = 编排器._读下限()
        目标 = sorted(下限)[0]
        文件表 = {键: _条目(值 + 5) for 键, 值 in 下限.items() if 键 != 目标}
        达标, 违规 = 编排器._检查逐文件下限(写JSON(文件表))
        assert not 达标
        assert (目标, None, 下限[目标]) in 违规

    def test_绝对路径与反斜杠都能对上(self, 编排器, 写JSON):
        """coverage 的 JSON 里路径形态随平台/调用方式变化，判定前要归一。"""
        下限 = 编排器._读下限()
        文件表 = {}
        for 序号, (键, 值) in enumerate(sorted(下限.items())):
            if 序号 % 2:
                形态 = os.path.join(仓库根, *键.split('/'))  # 绝对 + 平台分隔符
            else:
                形态 = 键.replace('/', os.sep)               # 相对 + 平台分隔符
            文件表[形态] = _条目(值 + 5)
        达标, 违规 = 编排器._检查逐文件下限(写JSON(文件表))
        assert 达标, '路径归一失败：%s' % (违规,)

    def test_未列入的文件不受点阈值约束(self, 编排器, 写JSON):
        """点阈值只保护点名文件，其余交给全局 fail_under（面阈值）。"""
        文件表 = {键: _条目(值 + 5) for 键, 值 in 编排器._读下限().items()}
        文件表['src/jikuai/某个没被点名的文件.py'] = _条目(3.0)
        达标, _ = 编排器._检查逐文件下限(写JSON(文件表))
        assert 达标


class Test覆盖率排除:
    """`覆盖率排除` 是覆盖率跑默认 `--ignore` 掉的子进程密集文件清单（W163）。

    它不进覆盖率统计，但**在「运行全部测试」那步全速跑过**，所以排除不等于放松门禁。
    这里的两条测试守的是这份清单不腐烂：条目改名/删除而没同步，只会在 CI 里
    以「--ignore 一个不存在的文件」静默失效，比表和现实脱节更隐蔽。
    """

    def test_条目都指向真实存在的文件(self, 编排器):
        缺失 = [文件 for 文件 in 编排器.覆盖率排除
                if not os.path.exists(os.path.join(仓库根, *文件.split('/')))]
        assert not 缺失, '覆盖率排除点名的文件不存在（改名/删除没同步）：%s' % 缺失

    def test_条目是tests下的posix相对路径(self, 编排器):
        """`--ignore=` 的实参要能在仓库根下对上，反斜杠或绝对路径都会静默失配。"""
        assert 编排器.覆盖率排除, '覆盖率排除是空的——那 --不排除 分支就成了摆设'
        for 文件 in 编排器.覆盖率排除:
            assert '\\' not in 文件, '必须用 posix 分隔符：%s' % 文件
            assert 文件.startswith('tests/'), '只该排除测试文件：%s' % 文件

    def test_不排除开关存在(self, 编排器):
        """`--不排除` 是核对「排除是否真的不掉点阈值」的唯一逃生口，删了就没法自证。"""
        源码 = open(os.path.join(仓库根, 'scripts', 'coverage_baseline.py'),
                    encoding='utf-8').read()
        assert "'--不排除'" in 源码


class Test其他:
    def test_下限文件缺失时不误判(self, 编排器, 写JSON, monkeypatch):
        """没有下限表就没有点阈值可卡，应当放行而不是把一切判成违规。"""
        monkeypatch.setattr(编排器, '下限文件',
                            os.path.join(仓库根, 'docs', '不存在的下限表.json'))
        达标, 违规 = 编排器._检查逐文件下限(写JSON({}))
        assert 达标 and 违规 == []
