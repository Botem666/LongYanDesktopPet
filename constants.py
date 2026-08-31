# -*- coding: utf-8 -*-
"""常量配置：GIF 分类字典"""

# GIF 按功能分类
# idle      — 待机：默认状态，启动时随机选一个
# reactions — 反应：单击时随机触发
# drag      — 拖拽：被拖动时播放
# actions   — 动作：双击可选的特殊动作
# typing    — 打字：检测到键盘输入时播放
GIF_CATEGORIES = {
    'idle': ['卖萌.gif', '吃爆米花.gif', '吃瓜.gif', '记笔记1.gif', '记笔记2.gif'],
    'reactions': ['哈喽.gif', '害羞.gif', '摇一摇.gif', '灵魂出窍.gif', '难绷.gif'],
    'drag': ['摇一摇.gif'],
    'actions': ['有钱.gif', '超级有钱.gif', '来喝一杯.gif', '比心.gif'],
    'typing': ['记笔记1.gif', '记笔记2.gif'],
}

# 所有 GIF 文件名（去重，用于坐标配置等）
GIF_NAMES = list(dict.fromkeys(name for names in GIF_CATEGORIES.values() for name in names))
