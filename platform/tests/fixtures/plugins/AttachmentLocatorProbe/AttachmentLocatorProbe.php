<?php
/** TEST ONLY observation, mounted and enabled exclusively by the isolated attachment CI runner. */
class AttachmentLocatorProbe extends \LimeSurvey\PluginManager\PluginBase
{
    protected static $description = 'TEST ONLY: redacted attachment locator observation';
    protected static $name = 'AttachmentLocatorProbe';
    public $allowedPublicMethods = [];

    public function init()
    {
        if (getenv('ADMIN_WEB_ATTACHMENT_LOCATOR_PROBE') !== '1' || !is_file('enabletests')) return;
        require_once __DIR__ . '/AttachmentLocatorProbeChecks.php';
        $this->subscribe('newDirectRequest', 'observe');
    }

    public function observe()
    {
        if ($this->getEvent()->get('target') !== 'MjyQuestionExtensions'
            || $this->getEvent()->get('function') !== 'attachmentFile') return;
        // Only in-memory locator inputs. Never capture the signature, timestamps or raw request.
        $query = array_intersect_key($_GET, array_flip(['sid','generation','responseId','field','storedName']));
        if (!attachmentProbeInput($query)) return;
        register_shutdown_function(function () use ($query) {
            if (http_response_code() !== 404) return;
            $level = ob_get_level();
            ob_start();
            set_error_handler(function ($severity) { throw new RuntimeException('probe_failed'); });
            try {
                $evidence = attachmentProbeSilently(function () use ($query) {
                    $db = App()->getDb();
                    $uploadDir = (string)App()->getConfig('uploaddir');
                    return attachmentProbeReadOnly($db, function () use ($db, $query, $uploadDir) {
                        return attachmentProbeChecks($db, $query, $uploadDir);
                    });
                });
                $this->record($evidence);
            } catch (Throwable $error) {
                try { $this->record(['stage'=>'probe_failed']); } catch (Throwable $ignored) { /* no output */ }
            } finally {
                restore_error_handler();
                while (ob_get_level() > $level) ob_end_clean();
            }
        });
    }

    protected function evidenceDirectory(): string
    {
        return '/tmp/attachment-locator-probe';
    }

    private function record(array $evidence): void
    {
        $directory = $this->evidenceDirectory();
        $mask = umask(0077);
        try {
            if (!is_dir($directory)) mkdir($directory, 0700);
            $path = $directory . '/attachment-locator-probe.json';
            if (file_exists($path)) {
                // More than one failed request cannot be attributed to the one expected fixture.
                file_put_contents($directory . '/multiple', '{"stage":"multiple_requests"}');
                return;
            }
            $stream = fopen($path, 'x');
            if ($stream === false) return;
            try { fwrite($stream, json_encode($evidence)); } finally { fclose($stream); }
        } finally {
            umask($mask);
        }
    }
}
