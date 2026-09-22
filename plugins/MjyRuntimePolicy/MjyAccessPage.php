<?php

/**
 * 访问密码页（ADR 0016 决定 5）。独立的最小页面：不依赖问卷主题，不把任何作答者输入回显到页面。
 *
 * 所有动态值都经 htmlspecialchars；表单带引擎的 CSRF 令牌（前台 POST 一律校验，
 * application/config/internal.php 'enableCsrfValidation'）。
 */
class MjyAccessPage
{
    public const PASSWORD_FIELD = 'mjy_access_password';

    public static function render(string $actionUrl, string $csrfName, string $csrfToken, ?string $error): string
    {
        $error = $error === null ? '' : '<p class="mjy-error" role="alert">' . self::escape($error) . '</p>';
        return '<!DOCTYPE html>'
            . '<html lang="zh-CN"><head><meta charset="utf-8">'
            . '<meta name="viewport" content="width=device-width, initial-scale=1">'
            . '<meta name="robots" content="noindex">'
            . '<title>请输入访问密码</title>'
            . '<style>'
            . 'body{font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;'
            . 'background:#f5f6f8;margin:0;padding:16px;color:#1f2329}'
            . 'main{max-width:420px;margin:12vh auto;background:#fff;border-radius:8px;padding:28px 24px;'
            . 'box-shadow:0 1px 3px rgba(0,0,0,.08)}'
            . 'h1{font-size:20px;margin:0 0 8px}p{line-height:1.6}'
            . 'input[type=password]{width:100%;box-sizing:border-box;font-size:16px;padding:10px;margin:12px 0;'
            . 'border:1px solid #c9ccd1;border-radius:6px}'
            . 'button{width:100%;font-size:16px;padding:10px;border:0;border-radius:6px;background:#1664ff;color:#fff}'
            . '.mjy-error{color:#d4380d}'
            . '</style></head><body><main id="mjy-access-password">'
            . '<h1>请输入访问密码</h1>'
            . '<p>本问卷需要访问密码，请向问卷发布方索取。</p>'
            . $error
            . '<form method="post" action="' . self::escape($actionUrl) . '" autocomplete="off">'
            . '<input type="hidden" name="' . self::escape($csrfName) . '" value="' . self::escape($csrfToken) . '">'
            . '<label for="mjy-password">访问密码</label>'
            . '<input id="mjy-password" type="password" name="' . self::PASSWORD_FIELD . '" required autofocus>'
            . '<button type="submit">进入问卷</button>'
            . '</form></main></body></html>';
    }

    private static function escape(string $value): string
    {
        return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    }
}
