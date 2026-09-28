<?php

/**
 * 一次成功的附件取件：给出路径与字节数，**不持有内容**（ADR 0015 增补四）。
 *
 * 与 {@see MjyChannelResponse}（状态 ＋ 完整响应体）并列：那一个用于 JSON 应答，
 * 这一个用于字节流。分成两个类型是刻意的——只要有一个类型能装下"整份文件的字节"，
 * 早晚就会有人把文件读进内存再 echo 出去，而那正是 RemoteControl 的
 * `get_uploaded_files` 犯的错。这里连放的地方都没有。
 *
 * 不可变：构造之后不改。
 */
class MjyChannelFile
{
    /** @var string */
    private $path;

    /** @var int */
    private $size;

    public function __construct(string $path, int $size)
    {
        $this->path = $path;
        $this->size = $size;
    }

    public function path(): string
    {
        return $this->path;
    }

    public function size(): int
    {
        return $this->size;
    }
}
