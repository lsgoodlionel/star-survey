"""品牌与多语言（WP-19 切片 19.3，契约 survey-branding-v1）。

``schema`` 校验并解析 ``branding`` 块，``translations`` 校验并解析 ``translations`` 块，
``compile`` 把品牌降成引擎主题选项。三个模块都不碰引擎，只产出编译器要用的不可变对象。
"""
