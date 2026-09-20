<?php

/**
 * 事件投递通道。实现必须在平台确认接收后才正常返回；任何失败都要抛异常，
 * 以便中继保留事件、下轮重试。
 */
interface MjyEventTransport
{
    /**
     * @param array<int, array<string, mixed>> $envelopes 事件信封，按 id 升序
     * @throws Throwable 投递未被平台确认
     */
    public function send(array $envelopes): void;
}
