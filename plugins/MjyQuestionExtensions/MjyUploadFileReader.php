<?php

/**
 * 从引擎的上传目录读一件作答者上传的字节（ADR 0019 决定 8）。
 *
 * 路径形如 `<uploaddir>/surveys/<sid>/files/<stored_name>`
 * （`application/controllers/UploaderController.php:64`）。
 *
 * **文件名是唯一的外部输入，因此挡两次**：先按白名单认（只允许引擎那套
 * `fu_<随机>` 会用到的字符，不含斜杠、不含点点），再把解析出来的真实路径与问卷目录比一遍。
 * 一个被改坏的 `stored_name` 就是一次任意文件读取——这不是理论风险：会话表里那一列
 * 是引擎写的，但读它的人（本类）不该假设写它的人永远没出过错。
 */
class MjyUploadFileReader
{
    /** 引擎重命名后的文件名形状；刻意不含 `/`、`\` 与 `..`。 */
    private const STORED_NAME_PATTERN = '/\A[A-Za-z0-9][A-Za-z0-9._-]{0,254}\z/D';

    /** @var string */
    protected $uploadRoot;

    public function __construct(string $uploadRoot)
    {
        $this->uploadRoot = rtrim($uploadRoot, '/');
    }

    /**
     * @return string|null 字节；文件名不合规、目录逃逸、文件不存在或读不出来时为 null
     */
    public function read(int $surveyId, string $storedName): ?string
    {
        if (preg_match(self::STORED_NAME_PATTERN, $storedName) !== 1) {
            return null;
        }
        // `..` 已被白名单挡在外面（模式里没有连续的点也无所谓——点是允许字符，
        // 所以这里仍然显式再判一次：白名单与语义判定各管各的，少一条都可能被将来的改动放过）。
        if (strpos($storedName, '..') !== false) {
            return null;
        }
        $directory = $this->uploadRoot . '/surveys/' . $surveyId . '/files';
        $path = $directory . '/' . $storedName;
        $real = realpath($path);
        $realDirectory = realpath($directory);
        if ($real === false || $realDirectory === false) {
            return null;
        }
        // 解析后的真实路径必须仍在问卷的上传目录里：符号链接也在这里被挡住。
        if (strpos($real, $realDirectory . '/') !== 0) {
            return null;
        }
        $bytes = @file_get_contents($real);
        return $bytes === false ? null : $bytes;
    }
}
