# -*- coding: utf-8 -*-
"""极快语言标准库 — 校验模块。

提供中国特色的证件/号码校验函数：
- 身份证号（18位 GB11643）
- 手机号（含号段判断）
- 银行卡号（Luhn 校验）
- 车牌号（含新能源）
- 统一社会信用代码
"""


def 校验身份证(id_str):
    """校验 18 位身份证号码。返回 True/False。

    规则：
    1. 长度 18 位
    2. 前 17 位为数字
    3. 第 18 位为校验码（0-9 或 X）
    4. 加权求和 mod 11 对照校验码表
    """
    if not isinstance(id_str, str):
        id_str = str(id_str)
    id_str = id_str.strip().upper()
    if len(id_str) != 18:
        return False
    # 前 17 位必须是数字
    if not id_str[:17].isdigit():
        return False
    # 第 18 位是数字或 X
    if id_str[17] not in '0123456789X':
        return False
    # 加权因子
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_codes = '10X98765432'
    total = sum(int(id_str[i]) * weights[i] for i in range(17))
    expected = check_codes[total % 11]
    return id_str[17] == expected


def 提取身份证信息(id_str):
    """从身份证号提取信息。返回字典 {地区码, 出生日期, 性别, 有效}。"""
    if not isinstance(id_str, str):
        id_str = str(id_str)
    id_str = id_str.strip().upper()
    valid = 校验身份证(id_str)
    if not valid:
        return {'有效': False}
    area = id_str[:6]
    birth = f"{id_str[6:10]}-{id_str[10:12]}-{id_str[12:14]}"
    sex = '男' if int(id_str[16]) % 2 == 1 else '女'
    return {'地区码': area, '出生日期': birth, '性别': sex, '有效': True}


def 校验手机号(phone):
    """校验中国大陆手机号（11位，1开头）。"""
    if not isinstance(phone, str):
        phone = str(phone)
    phone = phone.strip()
    if len(phone) != 11:
        return False
    if not phone.isdigit():
        return False
    if phone[0] != '1':
        return False
    # 第二位有效号段
    if phone[1] not in '3456789':
        return False
    return True


def 判断运营商(phone):
    """根据手机号前三位判断运营商。"""
    if not isinstance(phone, str):
        phone = str(phone)
    phone = phone.strip()
    if len(phone) < 3 or not phone.isdigit():
        return '未知'
    prefix3 = phone[:3]
    # 移动号段
    mobile = {'134','135','136','137','138','139','147','148',
              '150','151','152','157','158','159','165',
              '172','178','182','183','184','187','188',
              '195','197','198'}
    # 联通号段
    unicom = {'130','131','132','145','146','155','156','166',
              '167','171','175','176','185','186','196'}
    # 电信号段
    telecom = {'133','149','153','173','174','177','180','181',
               '189','190','191','193','199'}
    if prefix3 in mobile:
        return '移动'
    elif prefix3 in unicom:
        return '联通'
    elif prefix3 in telecom:
        return '电信'
    return '未知'


def 校验银行卡(card_no):
    """Luhn 算法校验银行卡号。"""
    if not isinstance(card_no, str):
        card_no = str(card_no)
    card_no = card_no.strip().replace(' ', '')
    if not card_no.isdigit():
        return False
    if len(card_no) < 13 or len(card_no) > 19:
        return False
    digits = [int(d) for d in card_no]
    # Luhn: 从右往左，偶数位(从1开始计)乘2，>9 则减9
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def 校验车牌(plate):
    """校验中国车牌号（含新能源）。

    格式：
    - 普通：省份简称(1) + 字母(1) + 字母数字(5)  共7位
    - 新能源：省份简称(1) + 字母(1) + 字母数字(6) 共8位
    """
    if not isinstance(plate, str):
        plate = str(plate)
    plate = plate.strip().upper()
    provinces = '京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼宁'
    if len(plate) < 7 or len(plate) > 8:
        return False
    if plate[0] not in provinces:
        return False
    if not plate[1].isalpha():
        return False
    rest = plate[2:]
    if not all(c.isalnum() for c in rest):
        return False
    # 普通车牌 7 位，新能源 8 位
    if len(plate) == 7:
        return len(rest) == 5
    elif len(plate) == 8:
        return len(rest) == 6
    return False


def 校验社会信用代码(code):
    """校验 18 位统一社会信用代码。"""
    if not isinstance(code, str):
        code = str(code)
    code = code.strip().upper()
    if len(code) != 18:
        return False
    valid_chars = '0123456789ABCDEFGHJKLMNPQRTUWXY'
    for c in code:
        if c not in valid_chars:
            return False
    # 加权校验
    weights = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]
    char_map = {c: i for i, c in enumerate(valid_chars)}
    total = sum(char_map[code[i]] * weights[i] for i in range(17))
    remainder = 31 - total % 31
    if remainder == 31:
        remainder = 0
    expected = valid_chars[remainder]
    return code[17] == expected
