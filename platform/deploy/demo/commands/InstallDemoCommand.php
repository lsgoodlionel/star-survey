<?php

Yii::import('application.commands.InstallCommand');

class InstallDemoCommand extends InstallCommand
{
    public function run($args)
    {
        $username = (string) getenv('DEMO_ENGINE_ADMIN_USER');
        $password = (string) getenv('DEMO_ENGINE_ADMIN_PASSWORD');
        if ($username === '' || strlen($password) < 32) {
            fwrite(STDERR, "Demo engine credentials are missing or invalid.\n");
            return 1;
        }
        return parent::run(array($username, $password, 'Admin Web Demo', 'demo-admin@example.invalid'));
    }
}
