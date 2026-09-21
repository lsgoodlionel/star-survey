package cn.mjy.platform.engine;

/** 没有可用的引擎实例目录实现（租户模块未就绪），无法判定租户，只能拒收。 */
public class EngineDirectoryUnavailableException extends RuntimeException {

    public EngineDirectoryUnavailableException() {
        super("no EngineInstanceDirectory implementation is available");
    }
}
