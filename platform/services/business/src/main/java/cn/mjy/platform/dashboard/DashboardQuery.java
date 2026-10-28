package cn.mjy.platform.dashboard;

public record DashboardQuery(int surveyLimit, int taskLimit) {

    public static final int DEFAULT_LIMIT = 20;
    public static final int MAX_LIMIT = 50;

    public DashboardQuery {
        requireLimit("surveyLimit", surveyLimit);
        requireLimit("taskLimit", taskLimit);
    }

    public static DashboardQuery of(Integer surveyLimit, Integer taskLimit) {
        return new DashboardQuery(
                surveyLimit == null ? DEFAULT_LIMIT : surveyLimit,
                taskLimit == null ? DEFAULT_LIMIT : taskLimit);
    }

    private static void requireLimit(String name, int value) {
        if (value < 1 || value > MAX_LIMIT) {
            throw new DashboardInvalidRequestException(name + " must be between 1 and " + MAX_LIMIT);
        }
    }
}
