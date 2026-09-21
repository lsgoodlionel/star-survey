/**
 * 额度模块：套餐、订阅、额度判定、用量账本（Flyway V200–V299）。
 *
 * <h2>对外服务</h2>
 * <ul>
 *   <li>{@link cn.mjy.platform.entitlement.PlanCatalog}：发布与查询不可变的套餐版本；能力与计量登记。</li>
 *   <li>{@link cn.mjy.platform.entitlement.SubscriptionService}：按期追加订阅（试用、正式、取消），查询当前期与历史。</li>
 *   <li>{@link cn.mjy.platform.entitlement.EntitlementService#decide}：ALLOW / DENY / NEED_UPGRADE 加原因，只读不占额度。</li>
 *   <li>{@link cn.mjy.platform.entitlement.UsageLedger}：reserve -> capture | release，reclaimExpired，balance。</li>
 *   <li>{@link cn.mjy.platform.entitlement.ValidResponseMeter}：引擎事件模块专用的"有效完成答卷"计量接口。</li>
 * </ul>
 *
 * <h2>表</h2>
 * <table>
 *   <caption>数据归属与保护</caption>
 *   <tr><th>表</th><th>性质</th><th>运行期账号权限</th></tr>
 *   <tr><td>meter_definition、capability_definition</td><td>控制平面登记</td><td>只读</td></tr>
 *   <tr><td>plan_version</td><td>控制平面，发布后不可变（触发器对所有者同样生效）</td><td>SELECT、INSERT</td></tr>
 *   <tr><td>subscription</td><td>租户表，行级安全，只追加</td><td>SELECT、INSERT</td></tr>
 *   <tr><td>usage_ledger_entry</td><td>租户表，行级安全，只追加，事实来源</td><td>SELECT、INSERT</td></tr>
 *   <tr><td>usage_counter</td><td>租户表，行级安全，余额投影（条件更新）</td><td>SELECT、INSERT、UPDATE</td></tr>
 * </table>
 *
 * <h2>规则要点</h2>
 * <ul>
 *   <li>套餐是配置不是代码（U-07）：每个能力是独立开关，额度按计量配置；改套餐 = 发布新版本。</li>
 *   <li>订阅到期或取消后只读（P-04）：可读；导出窗口（套餐版本配置的天数）内可导出；写一律拒绝；数据从不自动删除。</li>
 *   <li>计费只计有效完成答卷（P-03），按问卷累计、永不归零，档位（10 万 / 20 万 / 30 万，P-02）由套餐额度配置。</li>
 *   <li>AI 用量按 能力 x 模型 x 计量 入账（U-02）；价目表不在本模块。</li>
 * </ul>
 */
package cn.mjy.platform.entitlement;
