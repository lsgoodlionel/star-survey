package cn.mjy.platform.asset;

/**
 * 一次拉取的结果。
 *
 * @param ingested 新入库的件数
 * @param skipped  已经拉过、这次跳过的件数（幂等）
 * @param refused  过不了平台上传闸门而被拒的件数——它们是坏文件，不是读取故障
 */
public record ResponderUploadPullResult(int ingested, int skipped, int refused) {
}
