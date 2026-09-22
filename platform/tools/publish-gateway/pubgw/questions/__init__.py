"""WP-02 题型扩展：题型专属的校验（check）、输入格式规则（formats）与编译降级（lower）。

与逻辑 DSL（pubgw/logic）一样只通过两处挂钩接入：validate.validate_definition 调
check_question_type，compiler 在逻辑降级之后调 lower_question_types。题型的列形状仍在
pubgw/qtypes.py。映射表与取舍见 platform/docs/p2/question-type-map.md。
"""
