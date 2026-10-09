<?php

Yii::import('application.commands.InstallCommand');

class ProductionInitCommand extends CConsoleCommand
{
    private const PLUGINS = array(
        'MjyPlatformBridge',
        'MjyRuntimePolicy',
        'MjyQuestionExtensions',
    );

    public function actionStatus()
    {
        $table = Yii::app()->db->tablePrefix . 'users';
        return Yii::app()->db->schema->getTable($table, true) === null ? 10 : 0;
    }

    public function actionInstall()
    {
        $username = $this->requiredEnvironment('ENGINE_ADMIN_USER');
        $email = $this->requiredEnvironment('ENGINE_ADMIN_EMAIL');
        $password = $this->readSecret('ENGINE_ADMIN_PASSWORD_FILE');
        if (strlen($password) < 32) {
            throw new CException('ENGINE_ADMIN_PASSWORD_FILE must contain at least 32 bytes.');
        }

        $command = new InstallCommand('install', $this->getCommandRunner());
        return $command->run(array($username, $password, 'Platform Operations', $email));
    }

    public function actionPlugins()
    {
        $manager = Yii::app()->getPluginManager();
        foreach (self::PLUGINS as $name) {
            $record = Plugin::model()->findByAttributes(array('name' => $name));
            $configPath = Yii::getPathOfAlias('webroot') . '/plugins/' . $name . '/config.xml';
            $config = ExtensionConfig::loadFromFile($configPath);
            if ($config === null) {
                throw new CException('Invalid plugin configuration: ' . $name);
            }
            if ($record === null) {
                [$installed, $error] = $manager->installPlugin($config, 'user');
                if (!$installed) {
                    throw new CException('Could not install plugin ' . $name . ': ' . $error);
                }
                $record = Plugin::model()->findByAttributes(array('name' => $name));
            }
            $record->version = (string) $config->xml->metadata->version;
            $record->active = 1;
            $record->load_error = 0;
            if (!$record->save()) {
                throw new CException('Could not activate plugin: ' . $name);
            }
            $plugin = $manager->loadPlugin($name, $record->id);
            if ($plugin !== null && method_exists($plugin, 'ensureSchema')) {
                $plugin->ensureSchema();
            }
        }

        SettingGlobal::setSetting('RPCInterface', 'json');
        SettingGlobal::setSetting('force_ssl', 'on');
        SettingGlobal::setSetting('ssl_disable_alert', '1');
        return 0;
    }

    private function requiredEnvironment($name)
    {
        $value = trim((string) getenv($name));
        if ($value === '') {
            throw new CException($name . ' is required.');
        }
        return $value;
    }

    private function readSecret($environmentName)
    {
        $path = $this->requiredEnvironment($environmentName);
        $value = @file_get_contents($path);
        if ($value === false || trim($value) === '') {
            throw new CException($environmentName . ' does not point to a readable secret.');
        }
        return rtrim($value, "\r\n");
    }
}
