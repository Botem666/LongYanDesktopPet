# -*- coding: utf-8 -*-
"""常量配置：GIF 分类字典"""

# GIF 按功能分类
# idle      — 待机：默认状态，启动时随机选一个
# reactions — 反应：单击时随机触发
# drag      — 拖拽：被拖动时播放
# actions   — 动作：双击可选的特殊动作
GIF_CATEGORIES = {
    'idle': ['吃薯片.gif', '喝奶茶.gif', '喝茶.gif', '纸箱.gif'],
    'reactions': ['害羞.gif', '喜欢.gif', '震惊.gif', '疑惑.gif', '无语.gif', '慌.gif',
                  '我擦.gif', '噗呲.gif', '哼哼.gif', '阴险.gif', '端详.gif', '灵光.gif',
                  '举牌.gif', '口水.gif', 'yes.gif', '思考.gif', '生气.gif'],
    'drag': ['抱抱.gif', '扶墙.gif', '想要.gif', '戳戳.gif', '对手指.gif', '拉泪.gif'],
    'actions': ['中指.gif', '点赞.gif', '鼓掌.gif', '上吊.gif', '上工.gif', '吐舌.gif',
                '扣脸.gif', '比x.gif', '浇水.gif', '捏脸.gif', '捏鼻.gif'],
}

# 所有 GIF 文件名（扁平列表，用于坐标配置等）
GIF_NAMES = [name for names in GIF_CATEGORIES.values() for name in names]
