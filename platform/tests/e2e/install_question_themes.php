<?php

/**
 * 把平台自带的题型主题（themes/question/mjy-*）装进引擎的 lime_question_themes。
 *
 * `.lss` 只带 question_theme_name 这个字符串，目标实例没装该主题时引擎会**静默**
 * 换成基础主题（ADR 0006 决定 6）。正式部署由镜像预装，端到端测试在这里现装。
 *
 * 在 survey-test-web 里运行：
 *   php platform/tests/e2e/install_question_themes.php [name ...]
 *
 * 不带参数＝装所有 mjy-* 主题。输出 JSON：{"installed":[{"name":..,"type":..,"path":..}],"errors":[..]}
 */

declare(strict_types=1);

require_once __DIR__ . '/question_slice_support.php';

bootstrapEngine();
exit(main(array_slice($argv, 1)));

/**
 * @param string[] $wanted
 */
function main(array $wanted): int
{
    $root = realpath(__DIR__ . '/../../..');
    $installed = [];
    $errors = [];
    foreach (manifestFolders($root . '/themes/question') as $folder) {
        $name = basename(themeRoot($folder, $root . '/themes/question'));
        if ($wanted !== [] && !in_array($name, $wanted, true)) {
            continue;
        }
        $relative = ltrim(substr($folder, strlen($root)), '/');
        try {
            $theme = QuestionTheme::model()->findByAttributes([], 'name = :name AND xml_path = :path', [
                ':name' => $name,
                ':path' => $relative,
            ]) ?? new QuestionTheme();
            $theme->importManifest($relative, true, true);
            $installed[] = ['name' => $name, 'path' => $relative];
        } catch (Throwable $exception) {
            $errors[] = sprintf('%s: %s', $relative, $exception->getMessage());
        }
    }
    echo json_encode(['installed' => $installed, 'errors' => $errors], JSON_UNESCAPED_SLASHES), "\n";
    return $errors === [] ? 0 : 1;
}

/**
 * 每个 config.xml 是一个可安装的单元（一个主题目录可以覆盖多个基础题型）。
 *
 * @return string[]
 */
function manifestFolders(string $themeRoot): array
{
    $folders = [];
    $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($themeRoot));
    foreach ($iterator as $info) {
        if ($info->getFilename() !== 'config.xml') {
            continue;
        }
        $folder = dirname((string) $info->getPathname());
        if (strpos(basename(themeRoot($folder, $themeRoot)), 'mjy-') === 0) {
            $folders[] = $folder;
        }
    }
    sort($folders);
    return $folders;
}

/** 从 config.xml 所在目录回溯到 themes/question/<name>。 */
function themeRoot(string $folder, string $themeRoot): string
{
    $relative = trim(substr($folder, strlen($themeRoot)), '/');
    $parts = explode('/', $relative);
    return $themeRoot . '/' . ($parts[0] ?? '');
}
