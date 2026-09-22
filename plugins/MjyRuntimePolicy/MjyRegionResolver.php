<?php

/**
 * IP → 地区码（ISO 3166-1 alpha-2，或 3166-2 如 CN-BJ）的可插拔数据源（ADR 0016 决定 8）。
 *
 * 仓库不附带任何 GeoIP 数据集。查不到返回 null，由策略的 regionUnknown 决定拒绝或放行。
 */
interface MjyRegionResolver
{
    public function resolve(string $ip): ?string;
}
