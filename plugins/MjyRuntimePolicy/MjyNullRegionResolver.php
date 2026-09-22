<?php

/**
 * 没有配置地区数据源时的实现：什么都查不到。
 */
class MjyNullRegionResolver implements MjyRegionResolver
{
    public function resolve(string $ip): ?string
    {
        return null;
    }
}
