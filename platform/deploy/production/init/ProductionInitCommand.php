<?php

Yii::import('application.commands.InstallCommand');

class ProductionInitCommand extends CConsoleCommand
{
    private const COMPLETED_SETTING = 'mjy_production_init_v1';

    private const PLUGINS = array(
        'MjyPlatformBridge',
        'MjyRuntimePolicy',
        'MjyQuestionExtensions',
    );

    public function actionStatus()
    {
        $prefix = Yii::app()->db->tablePrefix;
        $required = array('users', 'permissions', 'settings_global', 'plugins');
        $prefixedTables = 0;
        foreach (Yii::app()->db->schema->getTableNames() as $tableName) {
            if (strpos($tableName, $prefix) === 0) {
                $prefixedTables++;
            }
        }
        $present = 0;
        foreach ($required as $name) {
            if (Yii::app()->db->schema->getTable($prefix . $name, true) !== null) {
                $present++;
            }
        }
        if ($prefixedTables === 0) {
            return 10;
        }
        if ($present !== count($required)) {
            fwrite(STDERR, "Partial LimeSurvey schema detected; restore or repair the database, then rerun engine-init.\n");
            return 30;
        }

        $problems = $this->readinessProblems(true);
        if (count($problems) === 0) {
            return 0;
        }
        fwrite(STDERR, 'Incomplete production initialization: ' . implode(', ', $problems) . ". Rerun engine-init to repair it.\n");
        return 20;
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

    public function actionAdmin()
    {
        $username = $this->requiredEnvironment('ENGINE_ADMIN_USER');
        $email = $this->requiredEnvironment('ENGINE_ADMIN_EMAIL');
        $user = User::model()->findByAttributes(array('users_name' => $username));
        if ($user === null) {
            $password = $this->readSecret('ENGINE_ADMIN_PASSWORD_FILE');
            if (strlen($password) < 32) {
                throw new CException('ENGINE_ADMIN_PASSWORD_FILE must contain at least 32 bytes.');
            }
            $user = new User();
            $user->users_name = $username;
            $user->full_name = 'Platform Operations';
            $user->parent_id = 0;
            $user->lang = 'auto';
            $user->email = $email;
            $user->setPassword($password);
            if (!$user->save()) {
                throw new CException('Could not create the production administrator.');
            }
        }

        $attributes = array(
            'entity' => 'global',
            'entity_id' => 0,
            'uid' => $user->uid,
            'permission' => 'superadmin',
        );
        $permission = Permission::model()->findByAttributes($attributes);
        if ($permission === null) {
            $permission = new Permission();
            $permission->attributes = $attributes;
        }
        $permission->read_p = 1;
        if (!$permission->save()) {
            throw new CException('Could not grant production administrator permissions.');
        }
        return 0;
    }

    public function actionSettings()
    {
        $this->setRequiredSetting('RPCInterface', 'json');
        $this->setRequiredSetting('force_ssl', 'on');
        return 0;
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

        return 0;
    }

    public function actionComplete()
    {
        $problems = $this->readinessProblems(false);
        if (count($problems) !== 0) {
            throw new CException('Production initialization is incomplete: ' . implode(', ', $problems));
        }
        $this->setRequiredSetting(self::COMPLETED_SETTING, '1');
        return 0;
    }

    private function readinessProblems($requireMarker)
    {
        $problems = array();
        $username = $this->requiredEnvironment('ENGINE_ADMIN_USER');
        $user = User::model()->findByAttributes(array('users_name' => $username));
        if ($user === null) {
            $problems[] = 'administrator missing';
        } else {
            $permission = Permission::model()->findByAttributes(array(
                'entity' => 'global',
                'entity_id' => 0,
                'uid' => $user->uid,
                'permission' => 'superadmin',
                'read_p' => 1,
            ));
            if ($permission === null) {
                $problems[] = 'administrator permission missing';
            }
        }
        foreach (array('RPCInterface' => 'json', 'force_ssl' => 'on') as $name => $value) {
            $setting = SettingGlobal::model()->findByPk($name);
            if ($setting === null || (string) $setting->stg_value !== $value) {
                $problems[] = $name . ' setting missing';
            }
        }
        foreach (self::PLUGINS as $name) {
            $plugin = Plugin::model()->findByAttributes(array('name' => $name, 'active' => 1));
            if ($plugin === null) {
                $problems[] = $name . ' plugin inactive';
            }
        }
        $configuredVersion = (int) Yii::app()->getConfig('dbversionnumber');
        if (SettingGlobal::getDBVersionNumber() !== $configuredVersion) {
            $problems[] = 'database migration pending';
        }
        if ($requireMarker) {
            $marker = SettingGlobal::model()->findByPk(self::COMPLETED_SETTING);
            if ($marker === null || (string) $marker->stg_value !== '1') {
                $problems[] = 'completion marker missing';
            }
        }
        return $problems;
    }

    private function setRequiredSetting($name, $value)
    {
        $setting = SettingGlobal::setSetting($name, $value);
        if ($setting->hasErrors()) {
            throw new CException('Could not persist required setting: ' . $name);
        }
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
