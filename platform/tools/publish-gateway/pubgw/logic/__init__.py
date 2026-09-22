"""问卷逻辑 DSL（definitionVersion 2）：解析、类型检查、依赖检查、编译到 ExpressionScript。

    parser  文本 → 语法树（不 eval，不靠正则拆表达式）
    scope   题目引用的解析：代码／UUID → 题目、子题、尺度、类型、页码
    types   类型检查（数值／文本／布尔／单选域／多选集合）
    graph   向后引用、跨页引用、循环依赖
    emit    语法树 → ExpressionScript 受限子集（只用 .NAOK 变量与白名单函数）
    lower   v2 定义 → 引擎层定义（relevance、属性、转义后的文本）
    check   上述检查汇总成 ValidationIssue
"""
