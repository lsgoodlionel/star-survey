<?php

namespace ls\tests;

/**
 * 记录每一批投递内容的测试替身，可按批次模拟失败。
 */
class FakeEventTransport implements \MjyEventTransport
{
    /** @var array<int, array<int, array<string, mixed>>> */
    public $batches = [];

    /** @var \Throwable|null */
    public $failWith = null;

    /** @var int|null 第几批失败（从 1 开始） */
    public $failOnBatch = null;

    public function send(array $envelopes): void
    {
        $batchNumber = count($this->batches) + 1;
        if ($this->failWith !== null || $this->failOnBatch === $batchNumber) {
            throw $this->failWith ?? new \RuntimeException("batch $batchNumber failed");
        }
        $this->batches[] = $envelopes;
    }
}
