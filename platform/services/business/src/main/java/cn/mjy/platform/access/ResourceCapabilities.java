package cn.mjy.platform.access;

/** 当前操作者可用于资源工作区的权威动作能力；只返回布尔值，不暴露授权判定原因。 */
public record ResourceCapabilities(
        boolean canCreateProject,
        boolean canCreateChildren,
        boolean canEdit,
        boolean canSubmitApproval,
        boolean canPublishDirectly,
        boolean canApprovePublish,
        boolean canArchive,
        boolean canRestore) {
}
