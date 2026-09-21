package cn.mjy.platform.access;

/** 成员类别：员工占用席位；参与者（答题者、考生）不占席位，只能被授予不需要席位的角色。 */
public enum MemberKind {
    STAFF(true),
    PARTICIPANT(false);

    private final boolean usesSeat;

    MemberKind(boolean usesSeat) {
        this.usesSeat = usesSeat;
    }

    public boolean usesSeat() {
        return usesSeat;
    }
}
