package cn.mjy.platform.access;

/** 席位已用完（或额度模块未提供席位上限）。 */
public class SeatLimitExceededException extends RuntimeException {

    public SeatLimitExceededException(String message) {
        super(message);
    }
}
