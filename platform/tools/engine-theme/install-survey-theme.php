<?php

/**
 * Install a survey theme that ships with the image into the engine database.
 *
 * Run inside the engine container, from the repository root:
 *
 *     php platform/tools/engine-theme/install-survey-theme.php zh-business
 *
 * Why this exists: `Template::getTemplateList()` — the lookup behind
 * `Template::checkIfTemplateExists()`, which `import_helper.php` calls before it will
 * accept the `<themes>` section of an LSS, and behind theme resolution at render time —
 * only returns themes that have BOTH a directory under `themes/survey/` AND a row in
 * `lime_templates` with a global `lime_template_configuration` row. The installer seeds
 * those rows only for the themes upstream ships (`LsDefaultDataSets::getTemplatesData()`),
 * so a theme we add to the image is invisible until someone registers it. The admin UI
 * has an "Install" button for this; deployments and tests need it without a browser.
 *
 * This reads the same manifest the admin UI reads and writes the same two rows.
 * It deliberately does not call `TemplateManifest::importManifest()`: that one reaches
 * for `App()->user->name` / `->id`, which do not exist in a console application.
 *
 * Idempotent: re-running refreshes the rows from the manifest.
 * Prints {"theme": "<name>", "installed": true, "created": true|false}.
 */

declare(strict_types=1);

const OWNER_ID = 1;
const AUTHOR = 'MJY Platform';

exit(main($argv));

function main(array $argv): int
{
    $name = (string) ($argv[1] ?? '');
    if ($name === '' || !preg_match('/\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\z/', $name)) {
        fwrite(STDERR, "usage: php install-survey-theme.php <theme-directory-name>\n");
        return 2;
    }
    if (!is_file("themes/survey/$name/config.xml")) {
        fwrite(STDERR, "no manifest at themes/survey/$name/config.xml\n");
        return 2;
    }

    bootstrap();

    $manifest = Template::getTemplateConfiguration($name, null, null, true);
    $parent = (string) $manifest->config->metadata->extends;
    if ($parent !== '' && Template::model()->findByPk($parent) === null) {
        fwrite(STDERR, "cannot install '$name': the theme it extends ('$parent') is not installed\n");
        return 1;
    }

    $created = saveTemplate($name, $manifest, $parent);
    saveConfiguration($name, $manifest, $parent);

    // getTemplateList() is memoised per request and the engine caches the manifest
    // on disk; drop the cache so the very next request sees the new theme.
    clearCache();

    echo json_encode(['theme' => $name, 'installed' => true, 'created' => $created]), "\n";
    return 0;
}

function saveTemplate(string $name, $manifest, string $parent): bool
{
    $metadata = $manifest->config->metadata;
    $template = Template::model()->findByPk($name);
    $created = $template === null;
    if ($created) {
        $template = new Template();
        $template->name = $name;
        $template->creation_date = date('Y-m-d H:i:s');
    }
    $template->folder = $name;
    $template->title = (string) $metadata->title ?: $name;
    $template->author = AUTHOR;
    $template->author_email = '';
    $template->author_url = '';
    $template->copyright = (string) $metadata->copyright;
    $template->license = (string) $metadata->license;
    $template->version = (string) $metadata->version;
    $template->api_version = (string) $metadata->apiVersion;
    $template->view_folder = engineValue($manifest, 'viewdirectory', 'views');
    $template->files_folder = engineValue($manifest, 'filesdirectory', 'files');
    $template->description = (string) $metadata->description;
    $template->last_update = date('Y-m-d H:i:s');
    $template->owner_id = OWNER_ID;
    $template->extends = $parent;
    if (!$template->save()) {
        throw new RuntimeException('cannot save lime_templates row: ' . json_encode($template->getErrors()));
    }
    return $created;
}

function saveConfiguration(string $name, $manifest, string $parent): void
{
    $configuration = TemplateConfiguration::model()->find(
        'template_name = :name AND sid IS NULL AND gsid IS NULL AND uid IS NULL',
        [':name' => $name]
    );
    if ($configuration === null) {
        $configuration = new TemplateConfiguration();
        $configuration->template_name = $name;
    }
    // Files and the css framework are inherited from the mother theme unless this
    // manifest states them; `null` in the column means "use the mother's".
    $files = $manifest->config->files;
    $configuration->files_css = is_object($files)
        ? TemplateConfig::formatToJsonArray(TemplateManifest::formatArrayFields($manifest, 'files', 'css')) : null;
    $configuration->files_js = is_object($files)
        ? TemplateConfig::formatToJsonArray(TemplateManifest::formatArrayFields($manifest, 'files', 'js')) : null;
    $configuration->files_print_css = is_object($files)
        ? TemplateConfig::formatToJsonArray(TemplateManifest::formatArrayFields($manifest, 'files', 'print_css')) : null;

    $engineOwner = $parent !== '' ? TemplateManifest::getTemplateForXPath($manifest, 'engine') : $manifest;
    $configuration->cssframework_name = (string) $engineOwner->config->engine->cssframework->name;
    $configuration->cssframework_css = TemplateConfig::formatToJsonArray(
        TemplateManifest::getAssetsToReplaceFormatted($engineOwner->config->engine, 'css')
    );
    $configuration->cssframework_js = TemplateConfig::formatToJsonArray(
        TemplateManifest::formatArrayFields($engineOwner, 'engine', 'cssframework_js')
    );
    $configuration->packages_to_load = TemplateConfig::formatToJsonArray(
        TemplateManifest::formatArrayFields($engineOwner, 'engine', 'packages')
    );
    $options = $manifest->config->options[0] ?? null;
    $configuration->options = TemplateConfig::convertOptionsToJson($options === null ? [] : $options);
    if (!$configuration->save()) {
        throw new RuntimeException(
            'cannot save lime_template_configuration row: ' . json_encode($configuration->getErrors())
        );
    }
}

function engineValue($manifest, string $field, string $fallback): string
{
    $owner = TemplateManifest::getTemplateForXPath($manifest, 'engine');
    $value = (string) $owner->config->engine->{$field};
    return $value !== '' ? $value : $fallback;
}

function clearCache(): void
{
    $directory = 'tmp/runtime/cache';
    if (!is_dir($directory)) {
        return;
    }
    $items = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($directory, FilesystemIterator::SKIP_DOTS),
        RecursiveIteratorIterator::CHILD_FIRST
    );
    foreach ($items as $item) {
        $item->isDir() ? @rmdir($item->getPathname()) : @unlink($item->getPathname());
    }
}

function bootstrap(): void
{
    define('BASEPATH', '.');
    define('EXT', '.php');
    defined('STDIN') or define('STDIN', fopen('php://stdin', 'r'));
    require_once 'vendor/autoload.php';
    require_once 'vendor/yiisoft/yii/framework/yii.php';

    $settings = require 'application/config/config-defaults.php';
    $config = require 'application/config/internal.php';
    $config['components']['session']['class'] = 'ConsoleHttpSession';
    $config['components']['session']['cookieMode'] = 'none';
    $config['components']['session']['cookieParams'] = [];
    if (isset($config['config'])) {
        $settings = array_merge($settings, $config['config']);
    }
    unset($config['defaultController'], $config['config']);
    $config['runtimePath'] = $settings['tempdir'] . '/runtime';

    require_once 'application/core/ConsoleApplication.php';
    Yii::createApplication('ConsoleApplication', $config);
    Yii::app()->loadHelper('common');
}
